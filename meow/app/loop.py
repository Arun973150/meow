"""Meow - everything, wired together.

Tap Ctrl+M and talk. It routes what you said, answers out loud, looks at your
screen when the question needs it, points at things, and presses them after
asking. Tap Pause at any moment and it all stops.

    python scripts/meow.py
    python scripts/meow.py --mute            # no speech, bubble only
    python scripts/meow.py --no-jev          # keyword routing, no Jev calls

Ctrl+C to quit. Run scripts/check_keys.py first.

The path a sentence takes:

    Ctrl+M -> microphone -> AssemblyAI ---> interim text -> Jev (in parallel)
                                     \\
                                      end_of_turn -> route
                                                      |
              answer --> gpt-4o-mini + screenshot ----+
              show   --> UIA digest -> point at it
              act    --> UIA digest -> ask -> press it
              plan   --> broken into steps, run one at a time

Jev runs on interim transcripts, so its ~420ms lands while the user is still
speaking rather than after. Everything slow is on a worker thread; the render
loop draws the cat at 60fps and never waits for anything.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import replace
import queue
import sys
import threading
import time
from pathlib import Path

# No sys.path juggling: this is a package module now, imported rather
# than executed from a directory. The line that was here pointed at
# meow/ once the file moved, which put `meow/platform/` ahead of the
# STDLIB `platform` module on the path - zstandard then died on
# platform.python_implementation(), from an import three libraries
# away. Any package with a stdlib name is a trap like this waiting.

from meow.cat import (
    Animator, CatPalette, CatRenderer, CatState, rgba_to_premultiplied_bgra,
)
from meow.cat.bubble import (
    BubblePalette, BubbleRenderer, BubbleState, bubble_position,
)
from meow.cat.cursor import CatCursor
from meow.cat.follow import CursorFollower, FollowSettings, target_beside_cursor
from meow.work.agentdock import AgentDock
from meow.chat.launcher import ChatPanel
from meow.connectors import Outbox, Sender
from meow.config import MissingKey
from meow.console import quiet_library_warnings, use_utf8_console
from meow.agent.harness import Confirmation, Harness, enable_tracing
from meow.agent.memory import Memory
from meow.agent.mind import Mind
from meow.panic import DEFAULT_PANIC_KEY, Panic
from meow.agent.planner import Planner
from meow.platform.capture import capture_region, capture_screens, mean_luminance
from meow.desktop.uia import digest_foreground
from meow.platform.dpi import enable_per_monitor_dpi_awareness
from meow.platform.hotkey import HotkeyListener, HotkeyUnavailable
from meow.platform.monitors import get_cursor_position, get_virtual_desktop
from meow.platform.overlay import Bounds, Overlay
from meow.storage import migrate_old_layout, stuck_in_the_old_layout
from meow.language import (
    ADD_WORDS,
    ARTEFACT_WORDS,
    CLOSE_WORDS,
    MINIMUM_WORDS_FOR_A_PLAN,
    hears_yes,
    is_noise,
    spoken_words,
    starts_with_any,
    wants_its_own_window,
    without_trailing_yes_no,
)
from meow.language.routing import correct as correct_route
from meow.language.routing import too_short_to_hand_over
from meow.agent.router import Intent, Router
from meow.work.tasks import TaskRunner, TaskState, asking_confirmer
from meow.voice import AssemblyAIStreaming, ElevenLabsSpeaker, Microphone, SpeechQueue

TARGET_FPS = 60
HOME_MARGIN = 24
BACKGROUND_SAMPLE_SECONDS = 0.5
LUMINANCE_DEADBAND = 0.08
INTERIM_LINE_SECONDS = 1.5
REPLY_LINE_SECONDS = 4.0

# How long the cat waits for a spoken yes or no before giving up on a
# confirmation. Long enough to think, short enough that a forgotten question
# does not leave a tool call parked forever.
CONFIRM_TIMEOUT_SECONDS = 20.0

# How long after the cat asks a question that a short answer is taken
# seriously. "SRIJA." and "H." are noise in isolation and are the whole point
# when the cat has just said "spell it out for me".
ANSWER_WINDOW_SECONDS = 20.0

# After this long working with nothing said, the bubble stops being three dots
# and says so in words. A long turn is not broken - a big window, a full
# thread and a slow tool add up - but thirty seconds of identical dots reads as
# frozen, and the difference between "thinking" and "stuck" is the only thing
# the user actually wants to know.
SLOW_TURN_SECONDS = 6.0

# Saying one of these sends the draft that is waiting. Explicit words
# rather than a bare "yes": a bare yes carries no instruction, and the
# one irreversible thing here should need a sentence that could only
# mean this.
SEND_WORDS = ("send it", "send that", "send the email", "send it now",
              "go ahead and send", "yes send it", "send")
DISCARD_WORDS = ("discard it", "discard that", "delete the draft",
                 "do not send", "dont send", "never mind the email",
                 "bin it", "throw it away")

# Deliberately NOT here: "document" and "spreadsheet". They are ARTEFACT_WORDS
# - they mean "make me one" far more often than "read my Google one" - and
# upgrading them to ACT would skip the artefact upgrade that makes producing a
# file about a topic a plan.

def home_position(monitor, width: int, height: int) -> tuple[int, int]:
    return (monitor.work_right - width - HOME_MARGIN,
            monitor.work_bottom - height - HOME_MARGIN)


def main() -> None:
    use_utf8_console()
    quiet_library_warnings()

    # An existing install kept everything in one folder, including databases
    # in a Documents that Explorer does not show. Moved before anything opens
    # a database, and never merged: two conversation stores with overlapping
    # ids do not combine, and pretending they do loses messages quietly.
    moved = migrate_old_layout()
    if moved:
        print(f"  moved {len(moved)} file(s) into the new layout")
    stuck = stuck_in_the_old_layout()
    if stuck:
        # Named rather than shrugged at: the chat window holds
        # conversations.db, so the file that matters most is exactly the one
        # likely to be left, and two databases with no hint which is live is
        # the worst outcome of a move.
        print(f"  still in the old folder (something has them open): "
              f"{', '.join(stuck)}")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default="ctrl+m")
    parser.add_argument("--panic-key", default=DEFAULT_PANIC_KEY)
    parser.add_argument("--width", type=int, default=72)
    parser.add_argument("--mute", action="store_true")
    parser.add_argument("--no-jev", action="store_true",
                        help="keyword routing only, no Jev calls")
    parser.add_argument("--no-pointer", action="store_true")
    args = parser.parse_args()

    enable_per_monitor_dpi_awareness()
    desktop = get_virtual_desktop()
    monitor = desktop.primary
    if monitor is None:
        raise SystemExit("No monitors found.")

    panic = Panic()

    # The confirmation answer travels from the render loop back to the worker
    # thread that is blocked inside the agent.
    confirm_answer: dict[str, bool | None] = {"value": None}
    confirm_ready = threading.Event()
    awaiting_confirmation = threading.Event()

    def ask_out_loud(question: str) -> bool:
        """The Confirmer. Speaks the question and waits for a spoken reply."""
        replies.put(("ask", question))
        confirm_answer["value"] = None
        confirm_ready.clear()
        awaiting_confirmation.set()
        try:
            if not confirm_ready.wait(CONFIRM_TIMEOUT_SECONDS):
                return False  # silence is not consent
            return bool(confirm_answer["value"])
        finally:
            awaiting_confirmation.clear()

    # Before anything is built. The harness turns tracing on for itself, but
    # Mind is constructed first and every path is a ChatOpenAI now, so doing it
    # here is what makes the whole turn appear in one trace rather than only
    # the part that used tools.
    tracing = enable_tracing()

    # ONE memory for every path. Each used to remember separately - mind kept
    # four turns, the harness kept none at all, and tasks existed outside both -
    # which is why it felt random rather than forgetful.
    # Drafts waiting on the user, and the one thing that can send them.
    # The SENDER lives here rather than in the harness: the harness holds
    # the screen, which is private data and untrusted content, so giving
    # it an outbound channel would close the trifecta in one move. It
    # drafts; this sends, after a person has said so.
    outbox = Outbox()
    # `announce` so a login tab is never a surprise - the cat says it is
    # opening one before it does.
    sender = Sender(outbox,
                    announce=lambda text: replies.put(("say", text)))

    memory = Memory()

    # The record, and the window that reads it. The window is a separate
    # process and entirely optional - if it fails to start, or the user quits
    # it, the cat carries on and keeps writing. Nothing about hearing you
    # depends on a GUI being alive.
    panel = ChatPanel()
    panel.start()
    session = panel.begin("session", kind="chat", icon="cat")

    try:
        mind = Mind(memory=memory)
        harness = Harness(confirm=ask_out_loud, ask_before_acting=False,
                          memory=memory, budget=mind.screen.budget,
                          outbox=outbox)
        harness.on_note = lambda text: replies.put(("say", text))
        speech = None if args.mute else SpeechQueue(ElevenLabsSpeaker())
    except MissingKey as error:
        raise SystemExit(f"\n{error}\n")

    router = Router(use_jev=not args.no_jev, memory=memory)
    tasks = TaskRunner()
    # So "paste the results here" can reach what a task found. Without this the
    # harness had no idea a task had ever run, and answered that it could not
    # paste research results - which was true and unhelpful.
    harness.task_results = lambda: [
        (t.title, t.result or t.summary) for t in tasks.visible
        if (t.result or t.summary)
    ]
    planner = Planner(
        harness,
        # "quiet" is printed and never spoken. A plan the user did not ask to
        # have explained should leave the voice alone until it has something
        # to report, and let the thinking dots carry the meantime.
        on_event=lambda kind, text: replies.put(
            ("say" if kind in ("say", "step", "stopped") else kind, text)),
        should_stop=panic.should_stop,
    )

    # When a short reply should be taken seriously, because the cat asked for
    # one. Written on the reply side, read on the transcript side; both run in
    # the render loop, so a plain dict is enough.
    awaiting_answer = {"until": 0.0}

    # Drafts already put in the window, so a turn does not propose the
    # same one again every time it looks.
    shown_drafts: set[str] = set()

    # When the current turn started working, so a long one can say so in words
    # instead of showing the same three dots for half a minute.
    working_since = 0.0
    said_it_is_slow = False

    cat_cursor = None
    if not args.no_pointer:
        try:
            cat_cursor = CatCursor()
        except Exception as error:  # noqa: BLE001 - cosmetic, never fatal
            print(f"  cat cursor unavailable ({error})")

    renderer = CatRenderer(width=args.width)
    bubble_renderer = BubbleRenderer()
    bubble_state = BubbleState()
    follow_settings = FollowSettings()

    # An icon beside the cat for each running agent. In the tray first,
    # which does not work: Windows 11 hides new tray icons behind the
    # chevron and remembers that per icon, so every agent's icon would
    # start hidden and need un-hiding by hand, forever.
    dock = AgentDock()

    home_x, home_y = home_position(monitor, renderer.width, renderer.height)
    follower = CursorFollower(home_x, home_y, follow_settings)
    animator = Animator(state=CatState.SLEEPING)

    try:
        listener = HotkeyListener()
        hotkey = listener.register(args.key)
        panic_key = listener.register(args.panic_key)
    except (HotkeyUnavailable, ValueError) as error:
        raise SystemExit(f"\n{error}\n")

    microphone: Microphone | None = None
    transcriber: AssemblyAIStreaming | None = None
    active = False

    replies: queue.Queue[tuple[str, str]] = queue.Queue()
    working = threading.Event()

    # --- panic wiring, all local ----------------------------------------

    panic.on_panic("speech", lambda: speech and speech.clear())
    panic.on_panic("cursor", lambda: cat_cursor and cat_cursor.remove())
    panic.on_panic("confirmation", lambda: (
        confirm_answer.__setitem__("value", False), confirm_ready.set()))
    panic.on_panic("tasks", tasks.stop_all)

    def ask(transcript: str) -> None:
        """Route the sentence and run whichever path it asked for."""
        working.set()
        try:
            route = router.resolve(transcript)
            replies.put(("route", route.describe()))

            if panic.tripped:
                return

            # Two structural corrections, both because the cost of being
            # wrong is asymmetric rather than because a classifier is weak.
            # See meow/language/routing.py for what each one is fixing.
            route, why = correct_route(route, transcript)
            if why:
                print(f"          {why}")

            # A plan the user is watching does not need a window. Only work
            # they have walked away from does - which is what a window is FOR,
            # and putting one in front of a four-step desktop job made the cat
            # narrate something already on screen in front of them.
            if route.intent is Intent.PLAN and not wants_its_own_window(transcript):
                print("          multi-step, doing it here rather than handing over")
                plan = planner.run(transcript)
                if plan.abandoned:
                    replies.put(("say", "i could not break that into steps."))
                return

            # Downgraded before the plan branch, not inside it, so it falls
            # through to the ordinary path instead of spawning anything.
            # Handled here rather than in the router because the cost of the
            # mistake is asymmetric: a misrouted plan opens a window and a
            # thread, a misrouted act just answers.
            if route.intent is Intent.PLAN and too_short_to_hand_over(transcript):
                print("          too short to hand over, doing it here")
                route = replace(route, intent=Intent.ACT)

            if route.intent is Intent.PLAN:
                if not tasks.can_start():
                    replies.put(("say", "i am already working on as much as i "
                                        "can. say close that when one is done."))
                    return

                def work(task):
                    """Run the plan inside the task, reporting as it goes."""
                    actor = f"task {task.number}"
                    memory.join(actor, task.title)
                    # Its own conversation, with its own icon, so the sidebar
                    # separates handed-over work from the voice session.
                    thread = panel.begin(
                        task.title, kind="task",
                        icon="magnifier" if wants_its_own_window(task.goal)
                        else "gear")
                    # Hung on the task so the render loop can put an icon on
                    # screen for it and know which conversation to open.
                    task.conversation = thread

                    def report(kind, text):
                        task.log(text, kind="error" if kind == "error"
                                 else "step" if kind in ("step", "quiet")
                                 else "say")
                        # So the cat can answer "what is it doing" from shared
                        # memory, without asking the task - which may be
                        # mid-request and cannot be interrupted to reply.
                        memory.update(actor, text)
                        panel.say(thread, task.title, text)

                    # Its OWN harness, with a confirmer that declines rather
                    # than asking. Sharing the foreground one meant a task's
                    # permission question went to the voice loop - the task sat
                    # blocked for twenty seconds and the user, who had moved on,
                    # got "open excel?" out of nowhere.
                    # Shares the memory, so a step can resolve "the one we
                    # were just looking at" against what was actually said.
                    # It ASKS now rather than declining outright. The
                    # question goes into its own conversation, its icon turns
                    # amber, and the thread blocks until somebody answers or
                    # four minutes pass. Nobody is interrupted - which is what
                    # consent needs in order to mean anything.
                    confirmer = asking_confirmer(
                        task,
                        ask=lambda question: panel.ask(thread, question),
                        wait_for_answer=lambda marker, seconds:
                            panel.wait_for_answer(thread, marker, seconds))
                    # unattended: it asks ONLY about things that are hard
                    # to undo. Handing work over is consent to the ordinary
                    # steps of doing it, and a task that stops for each of
                    # those never finishes.
                    own = Harness(confirm=confirmer,
                                  ask_before_acting=False,
                                  memory=memory, actor=actor,
                                  budget=mind.screen.budget)
                    own.unattended = True
                    # unattended: its confirmer declines without asking,
                    # so a refusal must not be reported as the user saying no.
                    worker = Planner(own, on_event=report, unattended=True,
                                     should_stop=lambda: (panic.should_stop()
                                                          or task.should_stop))
                    plan = worker.run(task.goal)

                    # Anything said while it was working happens now, in order.
                    while True:
                        extra = task.take_instruction()
                        if extra is None or task.should_stop:
                            break
                        task.log(extra, kind="step")
                        worker.run(extra)

                    if plan.abandoned:
                        panel.end(thread)
                        return "could not break that into steps"

                    # Everything the steps produced, kept so "paste the results
                    # here" has something to paste. Without it the work exists
                    # only as sentences that have already been spoken.
                    task.result = "\n".join(
                        step.said for step in plan.steps if step.said.strip())

                    last = next((s.said for s in reversed(plan.steps)
                                 if s.said.strip()), "")
                    # Whatever it wrote, recorded as a file the window can
                    # offer to open. The sentence already says "saved as
                    # gpu_prices_india", which is not something anyone can
                    # click.
                    made = getattr(own, "_last_document", None)
                    if made is not None:
                        panel.produced(thread, str(made.path))

                    # What it could not do without you. Said rather than left
                    # in a log nobody reopens: a task that skips half its work
                    # and reports "done" is worse than one that fails.
                    if task.skipped:
                        needed = "; ".join(task.skipped[:3])
                        panel.say(thread, task.title,
                                  f"i could not do this without you: {needed}")

                    panel.end(thread)
                    return last or ("done" if plan.succeeded else "stopped early")

                task = tasks.spawn(transcript, work)
                print(f"          handed to task {task.number}")
                # Back to listening immediately. The window reports from here.
                replies.put(("say", "i am on it."))
                return

            if route.intent in (Intent.SHOW, Intent.ACT):
                # SHOW means being told, not having it done - "how do i change
                # my dns", "where is bluetooth". In that mode every tool that
                # changes anything refuses, so the cat explains and points
                # rather than pressing. Enforced in the tools rather than
                # asked for in the prompt.
                harness.guiding = route.intent is Intent.SHOW

                # The harness knows the controls on screen by name, so it never
                # produces a coordinate and cannot produce a wrong one.
                stream = harness.answer(transcript)
                while True:
                    try:
                        event = next(stream)
                    except StopIteration:
                        break
                    if panic.tripped:
                        break
                    if isinstance(event, Confirmation):
                        allowed = ask_out_loud(event.question)
                        stream = harness.respond(allowed)
                        continue
                    replies.put(("say", event))
                if harness.last_error:
                    replies.put(("error", harness.last_error))

                # Anything drafted this turn goes into the window, with
                # the exact recipient and body, so the decision is made
                # while looking at what will actually be sent.
                for draft in outbox.waiting():
                    if draft.id not in shown_drafts:
                        shown_drafts.add(draft.id)
                        panel.propose(session, draft)
                return

            # Plain answer. Screenshot only if the router thinks it is needed.
            shot = capture_screens()[0] if route.needs_screen else None
            for sentence in mind.answer(transcript, shot,
                                        crop_around=get_cursor_position()):
                if panic.tripped:
                    break
                replies.put(("say", sentence))
            if mind.last_error:
                replies.put(("error", mind.last_error))

        except Exception as error:  # noqa: BLE001 - reported, never fatal
            replies.put(("error", f"{type(error).__name__}: {error}"))
        finally:
            working.clear()
            replies.put(("done", ""))

    def shut_down_audio(mic, stt) -> None:
        def run() -> None:
            if mic is not None:
                mic.stop()
            if stt is not None:
                stt.stop()
        threading.Thread(target=run, name="audio-teardown", daemon=True).start()

    print(f"\n  tap {hotkey.display_name} and talk. Tap again to stop listening.")
    print(f"  tap {panic_key.display_name.upper()} to stop everything, instantly.")
    print("  long jobs get an icon top right - click it to watch them,")
    print("  say \"also ...\" to add to one, \"close that\" when done.")
    print(f"  routing: {'jev' if router.using_jev else 'keywords'}"
          f"{'  (' + (router.unavailable_reason or '')[:60] + ')' if not router.using_jev else ''}")
    print(f"  speech: {'muted' if args.mute else 'on'}")
    print(f"  tracing: {'langsmith' if tracing else 'off (no LANGSMITH_API_KEY)'}")
    print(f"  chat window: {panel.state}")
    print("  Ctrl+C to quit.\n")

    # Drawing faults are reported once each and never twice, so a bug that
    # happens every frame does not fill the terminal.
    seen_draw_faults: set[str] = set()

    def draw_fault(where: str, error: Exception) -> None:
        """A bad frame must not take a running task down with it.

        Before background tasks existed, a crash here cost a cat. Now it can
        cost half-finished work the user is waiting on, which is a much worse
        trade than a dropped frame. Still printed, in full, the first time -
        swallowing it silently is how a rendering bug survives to ship.
        """
        message = f"{where}: {type(error).__name__}: {error}"
        if message not in seen_draw_faults:
            seen_draw_faults.add(message)
            print(f"  draw fault, frame skipped - {message}")

    frame_budget = 1.0 / TARGET_FPS
    started = time.perf_counter()
    previous_frame_at = started
    next_background_sample = 0.0
    using_light_ink = False
    asked_at: float | None = None

    with listener, Overlay(Bounds(home_x, home_y, renderer.width,
                                  renderer.height)) as overlay, \
            Overlay(Bounds(home_x, home_y, 1, 1)) as bubble:
        overlay.show()
        try:
            while True:
                frame_started = time.perf_counter()
                elapsed = frame_started - started
                timestep = frame_started - previous_frame_at
                previous_frame_at = frame_started

                for pressed in listener.pump():
                    if pressed.identifier == panic_key.identifier:
                        stopped = panic.trip()
                        print(f"  {elapsed:5.1f}s  STOPPED ({', '.join(stopped)})")
                        bubble_state.say("stopped.", elapsed, seconds=2.0)
                        animator.set_state(CatState.IDLE, elapsed)
                        # Latched only for work in flight; the next thing the
                        # user says should be heard normally.
                        threading.Timer(0.4, panic.reset).start()
                        continue

                    if pressed.identifier != hotkey.identifier:
                        continue
                    active = not active
                    if active:
                        print(f"  {elapsed:5.1f}s  listening")
                        microphone = Microphone()
                        transcriber = AssemblyAIStreaming()
                        microphone.start()
                        transcriber.start(microphone.chunks())
                        animator.set_state(CatState.LISTENING, elapsed)
                    else:
                        print(f"  {elapsed:5.1f}s  stopped listening")
                        shut_down_audio(microphone, transcriber)
                        microphone = transcriber = None
                        if speech is not None:
                            speech.clear()
                        bubble_state.dismiss(elapsed)
                        animator.set_state(CatState.IDLE, elapsed)

                if transcriber is not None:
                    for transcript in transcriber.poll():
                        if not transcript.is_final:
                            # Jev routes this in the background, so the answer
                            # is usually ready before the sentence ends.
                            router.consider(transcript.text)
                            if not awaiting_confirmation.is_set() and not working.is_set():
                                bubble_state.say(transcript.text, elapsed,
                                                 seconds=INTERIM_LINE_SECONDS)
                            continue

                        if awaiting_confirmation.is_set():
                            decision = hears_yes(transcript.text)
                            if decision is None:
                                replies.put(("say", "say yes or no."))
                                continue
                            print(f"  {elapsed:5.1f}s  answered "
                                  f"{'yes' if decision else 'no'}")
                            confirm_answer["value"] = decision
                            confirm_ready.set()
                            continue

                        said = transcript.text
                        # A question just asked suspends the noise filter. The
                        # cat said "spell it out for me" and then ignored
                        # "SRIJA.", "Es." and "H." - every letter of the answer
                        # it had asked for.
                        answering = elapsed < awaiting_answer["until"]
                        if is_noise(said) and not answering:
                            # Not spoken to, not remembered, not routed.
                            print(f"  {elapsed:5.1f}s  (ignored: {said})")
                            continue
                        print(f"  {elapsed:5.1f}s  heard: {said}")
                        memory.said("user", said)
                        panel.say(session, "user", said)
                        if panel.first_words(session, said):
                            # The first thing said names the conversation, so
                            # the sidebar reads as a list of what was asked
                            # rather than six rows all called "session".
                            pass

                        # Before routing. "send it" is the one
                        # irreversible thing the user can say, and it must mean
                        # exactly this rather than whatever a model makes of it.
                        waiting_draft = outbox.newest_waiting()
                        if waiting_draft is not None and starts_with_any(
                                said, SEND_WORDS):
                            approved = outbox.approve(waiting_draft.id)
                            if approved is None:
                                replies.put(("say", "that one has gone "
                                                    "already."))
                            else:
                                print(f"          sending draft "
                                      f"{approved.id}")
                                replies.put(("say",
                                             sender.send(approved.id).lower()))
                            continue

                        if waiting_draft is not None and starts_with_any(
                                said, DISCARD_WORDS):
                            outbox.refuse(waiting_draft.id)
                            replies.put(("say", "thrown away, nothing sent."))
                            continue

                        if starts_with_any(said, CLOSE_WORDS):
                            for finished in tasks.visible:
                                if finished.state.finished:
                                    # Retired on dismissal rather than on
                                    # finishing: one the user has not looked at
                                    # is still worth asking about, one they
                                    # have dismissed is not, and leaving it in
                                    # would take room in every later prompt.
                                    memory.leave(f"task {finished.number}")
                            gone = tasks.dismiss_finished()
                            replies.put(("say", "closed." if gone
                                         else "nothing finished to close."))
                            continue

                        running = tasks.newest_running()
                        if running is not None and starts_with_any(said, ADD_WORDS):
                            # Queued behind the current step rather than
                            # applied now. Interrupting half-written work to
                            # change it produces neither the work nor the
                            # change.
                            running.add_instruction(said)
                            print(f"          queued onto task {running.number}")
                            replies.put(("say", "added to what i am doing."))
                            continue

                        asked_at = elapsed
                        animator.set_state(CatState.THINKING, elapsed)
                        working_since = elapsed
                        said_it_is_slow = False
                        # Dots until there is something to say. Routing and
                        # planning take a second or two together, and an empty
                        # bubble for that long reads as nothing happening.
                        bubble_state.think(elapsed)
                        if speech is not None:
                            speech.clear()
                        panic.reset()
                        threading.Thread(target=ask, args=(transcript.text,),
                                         name="turn", daemon=True).start()

                while True:
                    try:
                        kind, payload = replies.get_nowait()
                    except queue.Empty:
                        break
                    if kind == "route":
                        print(f"          route: {payload}")
                    elif kind == "ask":
                        print(f"          asks: {payload}")
                        bubble_state.say(payload, elapsed, seconds=8.0)
                        animator.set_state(CatState.SPEAKING, elapsed)
                        if speech is not None:
                            speech.enqueue(payload)
                    elif kind == "say":
                        payload = without_trailing_yes_no(payload)
                        if not payload.strip():
                            continue
                        if asked_at is not None:
                            print(f"  {elapsed:5.1f}s  says (+{elapsed-asked_at:.1f}s): {payload}")
                            asked_at = None
                        else:
                            print(f"          says: {payload}")
                        memory.said("meow", payload)
                        panel.say(session, "meow", payload)
                        if payload.rstrip().endswith("?"):
                            awaiting_answer["until"] = elapsed + ANSWER_WINDOW_SECONDS
                        bubble_state.say(payload, elapsed, seconds=REPLY_LINE_SECONDS)
                        animator.set_state(CatState.SPEAKING, elapsed)
                        if speech is not None:
                            speech.enqueue(payload)
                    elif kind == "quiet":
                        print(f"          {payload}")
                    elif kind == "error":
                        print(f"  error: {payload}")
                        bubble_state.say("something went wrong", elapsed)
                    elif kind == "done" and active:
                        animator.set_state(CatState.LISTENING, elapsed)

                for finished in tasks.newly_finished():
                    mark = {TaskState.DONE: "done",
                            TaskState.FAILED: "failed",
                            TaskState.STOPPED: "stopped"}[finished.state]
                    print(f"  {elapsed:5.1f}s  task {finished.number} {mark}: "
                          f"{finished.summary[:70]}")
                    memory.update(f"task {finished.number}",
                                  f"{mark}: {finished.summary[:90]}")
                    if finished.state is TaskState.DONE:
                        replies.put(("say", f"{finished.summary} say close "
                                            f"that when you want it gone."))
                    else:
                        replies.put(("say", f"that one {mark}. "
                                            f"{finished.summary[:70]}"))

                animator.speech_level = speech.level if speech is not None else 0.0

                cursor_x, cursor_y = get_cursor_position()
                if active:
                    cursor_monitor = desktop.monitor_at(cursor_x, cursor_y) or monitor
                    target_x, target_y = target_beside_cursor(
                        cursor_x, cursor_y, renderer.width, renderer.height,
                        cursor_monitor, follow_settings)
                else:
                    target_x, target_y = home_position(
                        monitor, renderer.width, renderer.height)
                    if (abs(follower.x - target_x) < 2
                            and animator.state is not CatState.SLEEPING
                            and not bubble_state.visible):
                        animator.set_state(CatState.SLEEPING, elapsed)

                follower.update(target_x, target_y, timestep)
                overlay.move_to(follower.x, follower.y)

                # Pinned to the right edge, NOT to the cat. The cat follows
                # the pointer and goes home again; icons that moved with it
                # could not be clicked without chasing them first.
                dock.sync([(task.conversation, task.title,
                            "magnifier" if wants_its_own_window(task.goal)
                            else "gear",
                            task.state is TaskState.WAITING)
                           for task in tasks.running
                           if getattr(task, "conversation", 0)])
                dock.layout(monitor)
                try:
                    dock.draw(elapsed)
                except Exception as error:  # noqa: BLE001 - see draw_fault
                    draw_fault("agent dock", error)

                # A turn that has gone quiet for a while says so. In the
                # bubble only - never spoken, because interrupting the user to
                # tell them nothing has happened yet is worse than the silence.
                if (working.is_set() and not said_it_is_slow
                        and working_since
                        and elapsed - working_since > SLOW_TURN_SECONDS):
                    said_it_is_slow = True
                    bubble_state.say("still working on that", elapsed,
                                     seconds=SLOW_TURN_SECONDS * 2)

                opened = dock.clicked()
                if opened is not None:
                    print(f"          opening the chat window on {opened}")
                    dock.open_conversation(opened)
                animator.travel_x = follower.travel_direction_x
                animator.travel_y = follower.travel_direction_y

                if elapsed >= next_background_sample:
                    next_background_sample = elapsed + BACKGROUND_SAMPLE_SECONDS
                    luminance = mean_luminance(capture_region(
                        overlay.bounds.left, overlay.bounds.top,
                        overlay.bounds.width, overlay.bounds.height))
                    threshold = 0.42 + (LUMINANCE_DEADBAND if using_light_ink
                                        else -LUMINANCE_DEADBAND)
                    if (luminance < threshold) != using_light_ink:
                        using_light_ink = luminance < threshold
                        renderer.palette = CatPalette.for_background(luminance)
                        bubble_renderer.palette = BubblePalette.for_background(luminance)

                bubble_state.update(elapsed, timestep)

                try:
                    overlay.draw(rgba_to_premultiplied_bgra(
                        renderer.render(animator.pose_at(elapsed))))
                except Exception as error:  # noqa: BLE001 - see draw_fault
                    draw_fault("cat", error)

                if bubble_state.visible:
                    if bubble_state.thinking:
                        width, height = bubble_renderer.measure_thinking()
                    else:
                        width, height = bubble_renderer.measure(bubble_state.text)
                    left, top, tail_on_right = bubble_position(
                        int(follower.x), int(follower.y), renderer.width,
                        width, height, monitor)
                    if bubble_state.thinking:
                        image = bubble_renderer.render_thinking(
                            elapsed, bubble_state.alpha, tail_on_right)
                    else:
                        image = bubble_renderer.render(
                            bubble_state.text, bubble_state.alpha, tail_on_right)
                    if image is not None:
                        bubble.set_bounds(Bounds(left, top, width, height))
                        bubble.draw(rgba_to_premultiplied_bgra(image))
                        bubble.show()
                else:
                    bubble.hide()

                # Background work is shown in the chat window now, not in
                # a small box drawn over the desktop. The old panels were
                # layered click-through overlays, so there was nothing to
                # click: the results were visible and unreachable, which is
                # the worst of both. Each running agent has a tray icon that
                # opens its conversation - see meow/chat/trays.py.

                remaining = frame_budget - (time.perf_counter() - frame_started)
                if remaining > 0:
                    time.sleep(remaining)

        except KeyboardInterrupt:
            print("\n  stopped.")
        finally:
            panic.trip()
            if microphone is not None:
                microphone.stop()
            if transcriber is not None:
                transcriber.stop()
            if speech is not None:
                speech.clear()
            if cat_cursor is not None:
                cat_cursor.remove()
            tasks.stop_all()
            # The session is over, so the record says so and the window goes.
            # Leaving it open would show a live conversation that nothing is
            # writing to any more, with no way to tell by looking.
            dock.close()
            panel.end(session)
            panel.stop()

    print(f"\n  spend: {mind.screen.budget.summary()}")


if __name__ == "__main__":
    main()
