"""Phase 0.5 - the cat, live on the desktop.

Puts the animated cat in the corner of the screen and cycles it through every
state, so each one can be judged in motion rather than in a contact sheet. A
static preview cannot show blink timing, the ease between states, or whether the
breathing reads as breathing.

The cat also samples the screen underneath itself and flips between dark and
light ink so it stays readable over any window. That works precisely because the
overlay is excluded from capture: it can photograph its own background without
photographing itself.

    python scripts/cat_demo.py
    python scripts/cat_demo.py --state listening
    python scripts/cat_demo.py --width 72 --corner top-right

Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meow.cat import (
    Animator, CatPalette, CatRenderer, CatState, rgba_to_premultiplied_bgra,
)
from meow.platform.capture import capture_region, mean_luminance
from meow.platform.dpi import enable_per_monitor_dpi_awareness
from meow.platform.monitors import get_virtual_desktop
from meow.platform.overlay import Bounds, Overlay

TARGET_FPS = 30
SECONDS_PER_STATE = 2.6
MARGIN = 24

# Backgrounds change when the user switches windows, not per frame. Sampling
# every frame would cost a screenshot 30 times a second to answer a question
# whose answer almost never changes.
BACKGROUND_SAMPLE_SECONDS = 0.75
# Hysteresis around the palette threshold. Without it, a background sitting near
# the boundary makes the cat flicker between black and white ink.
LUMINANCE_DEADBAND = 0.08


def corner_bounds(monitor, width: int, height: int, corner: str) -> Bounds:
    """Place the sprite in one corner of the monitor's WORK area.

    Work area rather than the full monitor rect, so the cat sits above the
    taskbar instead of behind it.
    """
    if "right" in corner:
        left = monitor.work_right - width - MARGIN
    else:
        left = monitor.work_left + MARGIN
    if "bottom" in corner:
        top = monitor.work_bottom - height - MARGIN
    else:
        top = monitor.work_top + MARGIN
    return Bounds(left=left, top=top, width=width, height=height)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=72)
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="0 means run until Ctrl+C")
    parser.add_argument("--state", choices=[s.value for s in CatState],
                        help="hold one state instead of cycling")
    parser.add_argument("--corner", default="bottom-right",
                        choices=["top-left", "top-right",
                                 "bottom-left", "bottom-right"])
    parser.add_argument("--ink", choices=["auto", "dark", "light"], default="auto",
                        help="auto samples the background and inverts to suit")
    args = parser.parse_args()

    enable_per_monitor_dpi_awareness()
    desktop = get_virtual_desktop()
    monitor = desktop.primary
    if monitor is None:
        raise SystemExit("No monitors found.")

    renderer = CatRenderer(width=args.width)
    bounds = corner_bounds(monitor, renderer.width, renderer.height, args.corner)

    states = list(CatState)
    held = CatState(args.state) if args.state else None
    animator = Animator(state=held or CatState.IDLE)

    print(f"\ncat at ({bounds.left},{bounds.top}) "
          f"{renderer.width}x{renderer.height} on {monitor.device_name}")
    print(f"holding state: {held.value}" if held
          else f"cycling {len(states)} states, {SECONDS_PER_STATE:.1f}s each")
    print("click-through, and hidden from its own screenshots. Ctrl+C to stop.\n")

    frame_budget = 1.0 / TARGET_FPS
    started = time.perf_counter()
    next_background_sample = 0.0
    using_light_ink = False
    state_index_announced = ""
    slowest_frame_ms = 0.0
    frame_count = 0

    if args.ink != "auto":
        using_light_ink = args.ink == "light"
        renderer.palette = CatPalette.for_background(0.1 if using_light_ink else 0.9)

    with Overlay(bounds) as overlay:
        overlay.show()
        try:
            while True:
                frame_started = time.perf_counter()
                elapsed = frame_started - started
                if args.seconds and elapsed >= args.seconds:
                    break

                if args.ink == "auto" and elapsed >= next_background_sample:
                    next_background_sample = elapsed + BACKGROUND_SAMPLE_SECONDS
                    luminance = mean_luminance(
                        capture_region(bounds.left, bounds.top,
                                       bounds.width, bounds.height)
                    )
                    threshold = 0.42 + (LUMINANCE_DEADBAND if using_light_ink
                                        else -LUMINANCE_DEADBAND)
                    wants_light = luminance < threshold
                    if wants_light != using_light_ink:
                        using_light_ink = wants_light
                        renderer.palette = CatPalette.for_background(luminance)
                        print(f"  {elapsed:5.1f}s  background luminance "
                              f"{luminance:.2f} -> "
                              f"{'light' if wants_light else 'dark'} ink")

                if held is None:
                    wanted = states[int(elapsed / SECONDS_PER_STATE) % len(states)]
                    animator.set_state(wanted, elapsed)
                    if wanted.value != state_index_announced:
                        state_index_announced = wanted.value
                        print(f"  {elapsed:5.1f}s  {state_index_announced}")
                    if wanted is CatState.POINTING:
                        # Sweep the direction, so the lean and gaze can be seen
                        # tracking a target rather than frozen.
                        animator.point_direction_x = 0.9 * math.sin(elapsed * 1.3)

                overlay.draw(rgba_to_premultiplied_bgra(
                    renderer.render(animator.pose_at(elapsed))
                ))
                overlay.pump_messages()

                frame_ms = (time.perf_counter() - frame_started) * 1000
                slowest_frame_ms = max(slowest_frame_ms, frame_ms)
                frame_count += 1

                remaining = frame_budget - (time.perf_counter() - frame_started)
                if remaining > 0:
                    time.sleep(remaining)
        except KeyboardInterrupt:
            print("\nstopped.")

    if frame_count:
        print(f"\n{frame_count} frames, {slowest_frame_ms:.1f} ms slowest, "
              f"budget is {frame_budget * 1000:.1f} ms at {TARGET_FPS}fps")


if __name__ == "__main__":
    main()
