"""The cat, activated by a key, following the cursor.

Press the activation key and the cat wakes, leaves its corner and trails the
pointer. Press it again and it goes home and dozes off. This is the shape of
Phase 0 activation without any of the voice parts attached yet.

    python scripts/companion_demo.py
    python scripts/companion_demo.py --key ctrl+space
    python scripts/companion_demo.py --key f9 --width 64

Ctrl+C to stop. The activation key is global - it works while any other window
has focus, which is the whole point of registering it rather than reading the
keyboard. It is TAPPED to toggle, never held: see hotkey.py on why that
distinction matters.
"""

from __future__ import annotations

import argparse
import sys
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
from meow.platform.capture import capture_region, mean_luminance
from meow.platform.dpi import enable_per_monitor_dpi_awareness
from meow.platform.hotkey import HotkeyListener, HotkeyUnavailable
from meow.platform.monitors import get_cursor_position, get_virtual_desktop
from meow.platform.overlay import Bounds, Overlay

TARGET_FPS = 60  # following looks stiff below this; the cat is cheap to draw
HOME_MARGIN = 24
BACKGROUND_SAMPLE_SECONDS = 0.5
LUMINANCE_DEADBAND = 0.08

# How long the cat stays awake with no cursor movement before dozing off.
IDLE_SLEEP_SECONDS = 12.0

# Stand-ins for real replies. Deliberately the shape the bubble is for: short
# confirmations, and numbers that are easier read than heard.
SAMPLE_LINES = [
    "listening",
    "opening word",
    "found it in downloads",
    "42 files, 1.3 GB",
    "that button saves without asking where",
]
SECONDS_BETWEEN_LINES = 4.5


def home_position(monitor, width: int, height: int) -> tuple[int, int]:
    """Bottom-right of the work area - above the taskbar, out of the way."""
    return (monitor.work_right - width - HOME_MARGIN,
            monitor.work_bottom - height - HOME_MARGIN)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default="ctrl+m",
                        help="activation combination, e.g. ctrl+m, ctrl+space, "
                             "alt+space, f9. Tapped to toggle, never held.")
    parser.add_argument("--width", type=int, default=72)
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="0 means run until Ctrl+C")
    parser.add_argument("--start-active", action="store_true")
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

    active = args.start_active
    if active:
        animator.set_state(CatState.LISTENING, 0.0)

    try:
        listener = HotkeyListener()
        hotkey = listener.register(args.key)
    except (HotkeyUnavailable, ValueError) as error:
        raise SystemExit(f"\n{error}\n")

    print(f"\npress {hotkey.name.upper()} to wake the cat. "
          f"Press it again to send it home.")
    print(f"cat is {renderer.width}x{renderer.height}, home is "
          f"({home_x},{home_y}) on {monitor.device_name}")
    print("it is click-through, so keep using the machine normally. "
          "Ctrl+C to stop.\n")

    bounds = Bounds(home_x, home_y, renderer.width, renderer.height)
    frame_budget = 1.0 / TARGET_FPS
    started = time.perf_counter()
    previous_frame_at = started
    next_background_sample = 0.0
    using_light_ink = False
    last_cursor = get_cursor_position()
    last_cursor_moved_at = 0.0

    # The bubble is a second layered window rather than part of the cat's.
    # It is sized to its text, so sharing one window would make the cat's
    # bounds jump around every time the text changed - and the follower would
    # inherit that jitter.
    bubble_bounds = Bounds(home_x, home_y, 1, 1)
    next_line_at = 0.0
    line_index = 0

    with listener, Overlay(bounds) as overlay, Overlay(bubble_bounds) as bubble:
        overlay.show()
        try:
            while True:
                frame_started = time.perf_counter()
                elapsed = frame_started - started
                timestep = frame_started - previous_frame_at
                previous_frame_at = frame_started

                if args.seconds and elapsed >= args.seconds:
                    break

                for pressed in listener.pump():
                    if pressed.identifier != hotkey.identifier:
                        continue
                    active = not active
                    print(f"  {elapsed:5.1f}s  "
                          f"{'activated' if active else 'dismissed'}")
                    if active:
                        animator.set_state(CatState.LISTENING, elapsed)
                        last_cursor_moved_at = elapsed
                    else:
                        animator.set_state(CatState.IDLE, elapsed)

                cursor_x, cursor_y = get_cursor_position()
                if (cursor_x, cursor_y) != last_cursor:
                    last_cursor = (cursor_x, cursor_y)
                    last_cursor_moved_at = elapsed
                    if active and animator.state is CatState.SLEEPING:
                        animator.set_state(CatState.LISTENING, elapsed)

                if active:
                    # Follow the cursor onto whichever monitor it is on, rather
                    # than dragging it back toward the primary one.
                    cursor_monitor = desktop.monitor_at(cursor_x, cursor_y) or monitor
                    target_x, target_y = target_beside_cursor(
                        cursor_x, cursor_y, renderer.width, renderer.height,
                        cursor_monitor, follow_settings,
                    )
                    if elapsed - last_cursor_moved_at > IDLE_SLEEP_SECONDS:
                        animator.set_state(CatState.SLEEPING, elapsed)
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

                # Stand-in for the voice loop: say a line now and then, and
                # animate the mouth while the bubble is up.
                if active and elapsed >= next_line_at:
                    next_line_at = elapsed + SECONDS_BETWEEN_LINES
                    line = SAMPLE_LINES[line_index % len(SAMPLE_LINES)]
                    line_index += 1
                    bubble_state.say(line, elapsed, seconds=2.8)
                    animator.set_state(CatState.SPEAKING, elapsed)
                    print(f"  {elapsed:5.1f}s  says: {line}")
                elif (active and not bubble_state.visible
                        and animator.state is CatState.SPEAKING):
                    animator.set_state(CatState.LISTENING, elapsed)
                elif not active:
                    bubble_state.dismiss(elapsed)

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


if __name__ == "__main__":
    main()
