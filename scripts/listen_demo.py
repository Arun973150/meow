"""Phase 0.6, first half - the cat hears you.

Tap the activation key and the cat wakes, follows the cursor, and starts
listening. Your words appear in its bubble as you speak, and the line is
confirmed when you stop. Tap again and it goes home.

    python scripts/listen_demo.py
    python scripts/listen_demo.py --key ctrl+space --width 64

Ctrl+C to stop. Needs ASSEMBLYAI_API_KEY in .env.

No model and no text to speech yet - this is the input half. The bubble stands
in for the reply so the loop can be judged end to end before either is wired.

A note on whose words those are: normally the bubble is the cat talking. While
listening it shows YOUR words instead, the way every voice assistant does, so
you can see it is hearing you correctly before it acts. Once the model is
attached, the bubble goes back to being the cat's.
"""

from __future__ import annotations

import argparse
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
from meow.platform.capture import capture_region, mean_luminance
from meow.platform.dpi import enable_per_monitor_dpi_awareness
from meow.platform.hotkey import HotkeyListener, HotkeyUnavailable
from meow.platform.monitors import get_cursor_position, get_virtual_desktop
from meow.platform.overlay import Bounds, Overlay
from meow.voice import AssemblyAIStreaming, Microphone

TARGET_FPS = 60
HOME_MARGIN = 24
BACKGROUND_SAMPLE_SECONDS = 0.5
LUMINANCE_DEADBAND = 0.08

# How long a confirmed line stays up before fading.
FINAL_LINE_SECONDS = 3.0
# Interim text is replaced constantly, so it only needs to outlive the gap
# between updates. Anything longer and it lingers after the user stops talking.
INTERIM_LINE_SECONDS = 1.5


def home_position(monitor, width: int, height: int) -> tuple[int, int]:
    return (monitor.work_right - width - HOME_MARGIN,
            monitor.work_bottom - height - HOME_MARGIN)


def shut_down_audio(microphone, transcriber) -> None:
    """Tear the audio stack down on a background thread.

    Closing the websocket takes about a second - it is a termination handshake,
    not a socket close. Doing that inline freezes the render loop for that whole
    second, so the cat stops mid-blink exactly when the user has just tapped the
    key and is watching it. The frozen cat is far more noticeable than a socket
    that closes a moment later.

    The microphone stops first, because ending its generator is what unblocks
    the transcriber rather than tearing the stream out from under it.
    """
    def run() -> None:
        if microphone is not None:
            microphone.stop()
        if transcriber is not None:
            transcriber.stop()

    threading.Thread(target=run, name="audio-teardown", daemon=True).start()


def main() -> None:
    use_utf8_console()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default="ctrl+m")
    parser.add_argument("--width", type=int, default=72)
    parser.add_argument("--device", type=int, default=None,
                        help="input device index; default is the system default")
    args = parser.parse_args()

    enable_per_monitor_dpi_awareness()
    desktop = get_virtual_desktop()
    monitor = desktop.primary
    if monitor is None:
        raise SystemExit("No monitors found.")

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

    print(f"\ntap {hotkey.display_name} and talk. Tap again to stop.")
    print("your words appear in the bubble as you speak. Ctrl+C to quit.\n")

    bounds = Bounds(home_x, home_y, renderer.width, renderer.height)
    frame_budget = 1.0 / TARGET_FPS
    started = time.perf_counter()
    previous_frame_at = started
    next_background_sample = 0.0
    using_light_ink = False
    last_final_text = ""

    with listener, Overlay(bounds) as overlay, \
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
                        print(f"  {elapsed:5.1f}s  listening...")
                        try:
                            microphone = Microphone(device=args.device)
                            transcriber = AssemblyAIStreaming()
                            microphone.start()
                            transcriber.start(microphone.chunks())
                        except MissingKey as error:
                            print(f"\n{error}\n")
                            active = False
                            microphone = transcriber = None
                            continue
                        animator.set_state(CatState.LISTENING, elapsed)
                    else:
                        print(f"  {elapsed:5.1f}s  stopped listening")
                        shut_down_audio(microphone, transcriber)
                        microphone = transcriber = None
                        bubble_state.dismiss(elapsed)
                        animator.set_state(CatState.IDLE, elapsed)

                if transcriber is not None:
                    if transcriber.last_error:
                        print(f"  stt error: {transcriber.last_error}")
                        transcriber.last_error = None

                    for transcript in transcriber.poll():
                        if transcript.is_final:
                            # This is the moment the model would be called.
                            # Deliberately NOT waiting for the formatted
                            # version - it arrives later and says the same
                            # thing with punctuation.
                            last_final_text = transcript.text
                            bubble_state.say(transcript.text, elapsed,
                                             seconds=FINAL_LINE_SECONDS)
                            animator.set_state(CatState.THINKING, elapsed)
                            print(f"  {elapsed:5.1f}s  heard: {transcript.text}")
                        else:
                            bubble_state.say(transcript.text, elapsed,
                                             seconds=INTERIM_LINE_SECONDS)

                    if (animator.state is CatState.THINKING
                            and not bubble_state.visible):
                        animator.set_state(CatState.LISTENING, elapsed)

                cursor_x, cursor_y = get_cursor_position()
                if active:
                    cursor_monitor = desktop.monitor_at(cursor_x, cursor_y) or monitor
                    target_x, target_y = target_beside_cursor(
                        cursor_x, cursor_y, renderer.width, renderer.height,
                        cursor_monitor, follow_settings,
                    )
                else:
                    target_x, target_y = home_position(
                        monitor, renderer.width, renderer.height
                    )
                    if (abs(follower.x - target_x) < 2
                            and animator.state is not CatState.SLEEPING):
                        animator.set_state(CatState.SLEEPING, elapsed)

                follower.update(target_x, target_y, timestep)
                overlay.move_to(follower.x, follower.y)
                animator.travel_x = follower.travel_direction_x
                animator.travel_y = follower.travel_direction_y

                if elapsed >= next_background_sample:
                    next_background_sample = elapsed + BACKGROUND_SAMPLE_SECONDS
                    luminance = mean_luminance(capture_region(
                        overlay.bounds.left, overlay.bounds.top,
                        overlay.bounds.width, overlay.bounds.height,
                    ))
                    threshold = 0.42 + (LUMINANCE_DEADBAND if using_light_ink
                                        else -LUMINANCE_DEADBAND)
                    wants_light = luminance < threshold
                    if wants_light != using_light_ink:
                        using_light_ink = wants_light
                        renderer.palette = CatPalette.for_background(luminance)
                        bubble_renderer.palette = BubblePalette.for_background(
                            luminance
                        )

                bubble_state.update(elapsed, timestep)

                overlay.draw(rgba_to_premultiplied_bgra(
                    renderer.render(animator.pose_at(elapsed))
                ))

                if bubble_state.visible:
                    width, height = bubble_renderer.measure(bubble_state.text)
                    left, top, tail_on_right = bubble_position(
                        int(follower.x), int(follower.y), renderer.width,
                        width, height, monitor,
                    )
                    image = bubble_renderer.render(
                        bubble_state.text, bubble_state.alpha, tail_on_right
                    )
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
            # On the way out, block: the process is about to exit and a daemon
            # thread would be killed mid-handshake, leaving the session to time
            # out on AssemblyAI's side instead of closing.
            if microphone is not None:
                microphone.stop()
            if transcriber is not None:
                transcriber.stop()

    if last_final_text:
        print(f"\nlast thing heard: {last_final_text}")


if __name__ == "__main__":
    main()
