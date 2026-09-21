"""Meow - the whole Phase 0 loop.

Tap the key, talk, and the cat answers out loud. It follows the cursor, sees
your screen when the question needs it, and its mouth moves to the audio it is
actually producing.

    python scripts/meow.py
    python scripts/meow.py --always-see      # attach a screenshot every turn
    python scripts/meow.py --mute            # no speech, bubble only

Ctrl+C to quit. Needs all three keys in .env - run scripts/check_keys.py.

The pipeline:

    Ctrl+M -> microphone -> AssemblyAI -> gpt-4o-mini -> ElevenLabs
                                 |              |
                            end_of_turn    sentence 1 spoken while
                            fires early    sentence 2 is still being written

Everything slow runs on a worker thread. The render loop draws the cat at 60fps
and never waits for a network call - a cat that freezes while thinking has
stopped being a companion and become a progress bar.
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
from meow.cat.follow import CursorFollower, FollowSettings, target_beside_cursor
from meow.config import MissingKey
from meow.console import use_utf8_console
from meow.mind import Mind
from meow.platform.capture import capture_region, capture_screens, mean_luminance
from meow.platform.dpi import enable_per_monitor_dpi_awareness
from meow.platform.hotkey import HotkeyListener, HotkeyUnavailable
from meow.platform.monitors import get_cursor_position, get_virtual_desktop
from meow.platform.overlay import Bounds, Overlay
from meow.voice import AssemblyAIStreaming, ElevenLabsSpeaker, Microphone, SpeechQueue

TARGET_FPS = 60
HOME_MARGIN = 24
BACKGROUND_SAMPLE_SECONDS = 0.5
LUMINANCE_DEADBAND = 0.08
FINAL_LINE_SECONDS = 3.0
INTERIM_LINE_SECONDS = 1.5
REPLY_LINE_SECONDS = 4.0


def home_position(monitor, width: int, height: int) -> tuple[int, int]:
    return (monitor.work_right - width - HOME_MARGIN,
            monitor.work_bottom - height - HOME_MARGIN)


def main() -> None:
    use_utf8_console()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default="ctrl+m")
    parser.add_argument("--width", type=int, default=72)
    parser.add_argument("--always-see", action="store_true",
                        help="attach a screenshot every turn. Costs ~2,833 "
                             "tokens each; the default only sends one when the "
                             "request needs it")
    parser.add_argument("--mute", action="store_true",
                        help="skip text to speech, show replies in the bubble")
    args = parser.parse_args()

    enable_per_monitor_dpi_awareness()
    desktop = get_virtual_desktop()
    monitor = desktop.primary
    if monitor is None:
        raise SystemExit("No monitors found.")

    try:
        mind = Mind()
        speech = None if args.mute else SpeechQueue(ElevenLabsSpeaker())
    except MissingKey as error:
        raise SystemExit(f"\n{error}\n")

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
    except (HotkeyUnavailable, ValueError) as error:
        raise SystemExit(f"\n{error}\n")

    microphone: Microphone | None = None
    transcriber: AssemblyAIStreaming | None = None
    active = False

    # Sentences from the model arrive on a worker thread and are consumed by the
    # render loop. A queue rather than a shared list because the render loop must
    # never take a lock that a network call could be holding.
    replies: queue.Queue[tuple[str, str]] = queue.Queue()
    thinking = threading.Event()

    def ask(transcript: str) -> None:
        """Run one model call on a worker thread, streaming sentences back."""
        thinking.set()
        try:
            shot = capture_screens()[0] if desktop.monitors else None
            cursor = get_cursor_position()
            for sentence in mind.answer(transcript, shot, crop_around=cursor,
                                        always_see=args.always_see):
                replies.put(("say", sentence))
            if mind.last_error:
                replies.put(("error", mind.last_error))
        except Exception as error:  # noqa: BLE001 - reported, never fatal
            replies.put(("error", f"{type(error).__name__}: {error}"))
        finally:
            thinking.clear()
            replies.put(("done", ""))

    def shut_down_audio(mic, stt) -> None:
        def run() -> None:
            if mic is not None:
                mic.stop()
            if stt is not None:
                stt.stop()
        threading.Thread(target=run, name="audio-teardown", daemon=True).start()

    print(f"\ntap {hotkey.display_name} and talk. Tap again to stop listening.")
    print(f"screenshots: {'every turn' if args.always_see else 'only when asked about the screen'}")
    print(f"speech: {'muted' if args.mute else 'on'}")
    print("Ctrl+C to quit.\n")

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
                        print(f"  {elapsed:5.1f}s  stopped")
                        shut_down_audio(microphone, transcriber)
                        microphone = transcriber = None
                        if speech is not None:
                            speech.clear()
                        bubble_state.dismiss(elapsed)
                        animator.set_state(CatState.IDLE, elapsed)

                if transcriber is not None:
                    for transcript in transcriber.poll():
                        if transcript.is_final:
                            print(f"  {elapsed:5.1f}s  heard: {transcript.text}")
                            bubble_state.say(transcript.text, elapsed,
                                             seconds=FINAL_LINE_SECONDS)
                            animator.set_state(CatState.THINKING, elapsed)
                            asked_at = elapsed
                            # Barge-in: a new question cancels the old answer.
                            if speech is not None:
                                speech.clear()
                            threading.Thread(
                                target=ask, args=(transcript.text,),
                                name="model-call", daemon=True,
                            ).start()
                        elif not thinking.is_set():
                            bubble_state.say(transcript.text, elapsed,
                                             seconds=INTERIM_LINE_SECONDS)

                while True:
                    try:
                        kind, payload = replies.get_nowait()
                    except queue.Empty:
                        break
                    if kind == "say":
                        if asked_at is not None:
                            print(f"  {elapsed:5.1f}s  says (+{elapsed-asked_at:.1f}s): {payload}")
                            asked_at = None
                        else:
                            print(f"          says: {payload}")
                        bubble_state.say(payload, elapsed,
                                         seconds=REPLY_LINE_SECONDS)
                        animator.set_state(CatState.SPEAKING, elapsed)
                        if speech is not None:
                            speech.enqueue(payload)
                    elif kind == "error":
                        print(f"  error: {payload}")
                        bubble_state.say("something went wrong", elapsed)
                    elif kind == "done" and active:
                        animator.set_state(CatState.LISTENING, elapsed)

                # The mouth follows the audio actually coming out of the
                # speaker, so it stops when the sound does rather than when a
                # timer says it should.
                animator.speech_level = speech.level if speech is not None else 0.0

                cursor_x, cursor_y = get_cursor_position()
                if active:
                    cursor_monitor = desktop.monitor_at(cursor_x, cursor_y) or monitor
                    target_x, target_y = target_beside_cursor(
                        cursor_x, cursor_y, renderer.width, renderer.height,
                        cursor_monitor, follow_settings,
                    )
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

                bubble_state.update(elapsed, timestep)

                overlay.draw(rgba_to_premultiplied_bgra(
                    renderer.render(animator.pose_at(elapsed))))

                if bubble_state.visible:
                    width, height = bubble_renderer.measure(bubble_state.text)
                    left, top, tail_on_right = bubble_position(
                        int(follower.x), int(follower.y), renderer.width,
                        width, height, monitor)
                    image = bubble_renderer.render(
                        bubble_state.text, bubble_state.alpha, tail_on_right)
                    if image is not None:
                        bubble.set_bounds(Bounds(left, top, width, height))
                        bubble.draw(rgba_to_premultiplied_bgra(image))
                        bubble.show()
                else:
                    bubble.hide()

                remaining = frame_budget - (time.perf_counter() - frame_started)
                if remaining > 0:
                    time.sleep(remaining)

        except KeyboardInterrupt:
            print("\nstopped.")
        finally:
            if microphone is not None:
                microphone.stop()
            if transcriber is not None:
                transcriber.stop()
            if speech is not None:
                speech.clear()

    print(f"\nspend this session: {mind.screen.budget.summary()}")


if __name__ == "__main__":
    main()
