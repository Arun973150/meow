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
import queue
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meow.cat import (
    Animator, CatPalette, CatRenderer, CatState, rgba_to_premultiplied_bgra,
)
from meow.cat.bubble import (
    BubblePalette, BubbleRenderer, BubbleState, bubble_position,
)
from meow.cat.cursor import CatCursor
from meow.cat.follow import CursorFollower, FollowSettings, target_beside_cursor
from meow.config import MissingKey
from meow.console import quiet_library_warnings, use_utf8_console
from meow.harness import Confirmation, Harness
from meow.mind import Mind
from meow.panic import DEFAULT_PANIC_KEY, Panic
from meow.planner import Planner
from meow.platform.capture import capture_region, capture_screens, mean_luminance
from meow.platform.dpi import enable_per_monitor_dpi_awareness
from meow.platform.hotkey import HotkeyListener, HotkeyUnavailable
from meow.platform.monitors import get_cursor_position, get_virtual_desktop
from meow.platform.overlay import Bounds, Overlay
from meow.router import Intent, Router
from meow.tasks import TaskRunner, TaskState
from meow.taskwindow import PanelPalette, TaskPanel, stack_positions
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

# Saying one of these while a task is running adds to it instead of
# starting something new. Explicit rather than inferred: guessing whether
# a sentence belongs to a running task gets it wrong in both directions,
# and being wrong means either a lost instruction or a hijacked one.
ADD_WORDS = ("also", "and also", "add", "as well", "on top of that",
             "tell it to", "make it", "include")
CLOSE_WORDS = ("close that", "close them", "close it", "dismiss",
               "get rid of", "clear that", "clear them", "close the task")

YES_WORDS = ("yes", "yeah", "yep", "sure", "go ahead", "do it", "okay", "ok",
             "please do", "confirm", "alright")
NO_WORDS = ("no", "nope", "don't", "do not", "stop", "cancel", "leave it",
            "never mind", "nevermind", "wait")


def hears_yes(text: str) -> bool | None:
    """Did they agree? None when it was neither.

    Checked in this order because "no" is a substring of "nope" but also of
    "not now" - and a false yes presses something nobody asked for, while a
    false no just asks again.
    """
    lowered = f" {text.lower().strip()} "
    if any(f" {word} " in lowered or lowered.strip().startswith(word)
           for word in NO_WORDS):
        return False
    if any(f" {word} " in lowered or lowered.strip().startswith(word)
           for word in YES_WORDS):
        return True
    return None


def starts_with_any(text: str, phrases) -> bool:
    lowered = " ".join(text.lower().split())
    return any(lowered.startswith(phrase) or f" {phrase} " in f" {lowered} "
               for phrase in phrases)


def home_position(monitor, width: int, height: int) -> tuple[int, int]:
    return (monitor.work_right - width - HOME_MARGIN,
            monitor.work_bottom - height - HOME_MARGIN)


def main() -> None:
    use_utf8_console()
    quiet_library_warnings()

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

    try:
        mind = Mind()
        harness = Harness(confirm=ask_out_loud, ask_before_acting=False)
        speech = None if args.mute else SpeechQueue(ElevenLabsSpeaker())
    except MissingKey as error:
        raise SystemExit(f"\n{error}\n")

    router = Router(use_jev=not args.no_jev)
    tasks = TaskRunner()
    task_panel = TaskPanel()
    # One layered window per task, created as needed and reused. Making
    # and destroying a window per frame flickers.
    task_overlays: dict[int, Overlay] = {}
    planner = Planner(
        harness,
        # "quiet" is printed and never spoken. A plan the user did not ask to
        # have explained should leave the voice alone until it has something
        # to report, and let the thinking dots carry the meantime.
        on_event=lambda kind, text: replies.put(
            ("say" if kind in ("say", "step", "stopped") else kind, text)),
        should_stop=panic.should_stop,
    )

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

            if route.intent is Intent.PLAN:
                if not tasks.can_start():
                    replies.put(("say", "i am already working on as much as i "
                                        "can. say close that when one is done."))
                    return

                def work(task):
                    """Run the plan inside the task, reporting as it goes."""
                    def report(kind, text):
                        task.log(text, kind="error" if kind == "error"
                                 else "step" if kind in ("step", "quiet")
                                 else "say")

                    worker = Planner(harness, on_event=report,
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
                        return "could not break that into steps"
                    last = next((s.said for s in reversed(plan.steps)
                                 if s.said.strip()), "")
                    return last or ("done" if plan.succeeded else "stopped early")

                task = tasks.spawn(transcript, work)
                print(f"          handed to task {task.number}")
                # Back to listening immediately. The window reports from here.
                replies.put(("say", "i am on it."))
                return

            if route.intent in (Intent.SHOW, Intent.ACT):
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
    print("  long jobs get their own window - say \"also ...\" to add to one,")
    print("  and \"close that\" when you are done with it.")
    print(f"  routing: {'jev' if router.using_jev else 'keywords'}"
          f"{'  (' + (router.unavailable_reason or '')[:60] + ')' if not router.using_jev else ''}")
    print(f"  speech: {'muted' if args.mute else 'on'}")
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
                        print(f"  {elapsed:5.1f}s  heard: {said}")

                        if starts_with_any(said, CLOSE_WORDS):
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
                        if asked_at is not None:
                            print(f"  {elapsed:5.1f}s  says (+{elapsed-asked_at:.1f}s): {payload}")
                            asked_at = None
                        else:
                            print(f"          says: {payload}")
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
                        task_panel.palette = PanelPalette.for_background(luminance)

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

                # One window per task, stacked up the right edge. Built on
                # demand and torn down when the task is dismissed.
                # NOT named `overlay`. It was, and it shadowed the cat's own
                # overlay for the rest of the frame - so the next cat draw
                # pushed a 72x50 sprite into a 280x59 task window and the whole
                # app died on "expected 66080 bytes, got 14400". The loop
                # variable outliving the loop is the oldest trap in Python.
                live = tasks.visible
                for task, left, top in stack_positions(live, task_panel, monitor):
                    panel_image = task_panel.render(task, phase=elapsed)
                    panel_bounds = Bounds(left, top, panel_image.width,
                                          panel_image.height)
                    task_overlay = task_overlays.get(task.number)
                    if task_overlay is None:
                        task_overlay = Overlay(panel_bounds)
                        task_overlays[task.number] = task_overlay
                        task_overlay.show()
                    task_overlay.set_bounds(panel_bounds)
                    try:
                        task_overlay.draw(
                            rgba_to_premultiplied_bgra(panel_image))
                    except Exception as error:  # noqa: BLE001 - see draw_fault
                        draw_fault(f"task {task.number}", error)

                for number in list(task_overlays):
                    if not any(task.number == number for task in live):
                        task_overlays.pop(number).close()

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
            for task_overlay in task_overlays.values():
                task_overlay.close()

    print(f"\n  spend: {mind.screen.budget.summary()}")


if __name__ == "__main__":
    main()
