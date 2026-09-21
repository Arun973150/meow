"""Phase 0.1 + 0.2 - the overlay, and proof that it hides from capture.

Puts a click-through shape on screen, then verifies the thing that is easy to
get wrong and expensive to debug later: that the overlay does NOT appear in
Meow's own screenshots.

The check is an A/B, not an assertion. Capture once with WDA_EXCLUDEFROMCAPTURE
on and once with it off, and compare. A test that only checks the "on" case
passes just as happily when the capture code is broken and returns black.

    python scripts/overlay_demo.py
    python scripts/overlay_demo.py --seconds 15

Nothing is clicked and no input is sent. The window is click-through, so the
machine stays usable while it runs.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meow.platform.capture import capture_region
from meow.platform.dpi import enable_per_monitor_dpi_awareness
from meow.platform.monitors import get_virtual_desktop
from meow.platform.overlay import Bounds, Overlay, premultiply

# A colour nothing else on a desktop is likely to be, so counting exact matches
# is a reliable way to ask "is our window in this screenshot?"
MARKER_BLUE, MARKER_GREEN, MARKER_RED = 255, 0, 255

OVERLAY_SIZE = 240


def count_marker_pixels(bgra: bytes) -> int:
    """How many pixels in this capture are exactly our marker colour."""
    count = 0
    for index in range(0, len(bgra), 4):
        if (bgra[index] == MARKER_BLUE
                and bgra[index + 1] == MARKER_GREEN
                and bgra[index + 2] == MARKER_RED):
            count += 1
    return count


def render_circle(size: int, phase: float) -> bytearray:
    """A pulsing marker disc. Stands in for the cat sprite until Phase 0.5.

    Deliberately soft-edged: anti-aliased edges are exactly where premultiplied
    alpha goes wrong, so if the halo bug ever comes back this shows it.
    """
    pixels = bytearray(size * size * 4)
    center = size / 2.0
    radius = center * (0.62 + 0.10 * math.sin(phase))

    for y in range(size):
        for x in range(size):
            distance = math.hypot(x - center, y - center)
            # One pixel of feathering at the boundary.
            coverage = max(0.0, min(1.0, radius - distance))
            if coverage <= 0.0:
                continue
            offset = (y * size + x) * 4
            pixels[offset] = MARKER_BLUE
            pixels[offset + 1] = MARKER_GREEN
            pixels[offset + 2] = MARKER_RED
            pixels[offset + 3] = int(255 * coverage)
    return premultiply(pixels)


def wait_pumping(overlay: Overlay, seconds: float) -> None:
    """Sleep while keeping the window responsive."""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        overlay.pump_messages()
        time.sleep(0.016)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=8.0,
                        help="how long to keep the overlay on screen after the test")
    args = parser.parse_args()

    dpi = enable_per_monitor_dpi_awareness()
    print(f"\nDPI awareness: {dpi.applied}")
    if not dpi.coordinates_are_physical:
        print("  WARNING: coordinates are virtualized. Clicks will miss on any")
        print("           monitor whose scale differs from the primary one.")

    desktop = get_virtual_desktop()
    print(f"\n{desktop}")
    for monitor in desktop.monitors:
        print(f"  {monitor}")
    if desktop.has_negative_origin:
        print("  note: virtual desktop origin is negative - a monitor sits left")
        print("        of or above the primary one. Coordinates go below zero.")

    primary = desktop.primary
    if primary is None:
        raise SystemExit("No monitors found.")

    bounds = Bounds(
        left=primary.left + (primary.width - OVERLAY_SIZE) // 2,
        top=primary.top + (primary.height - OVERLAY_SIZE) // 2,
        width=OVERLAY_SIZE,
        height=OVERLAY_SIZE,
    )
    print(f"\nOverlay at ({bounds.left},{bounds.top}) {bounds.width}x{bounds.height}")

    with Overlay(bounds) as overlay:
        overlay.draw(render_circle(OVERLAY_SIZE, 0.0))
        overlay.show()
        wait_pumping(overlay, 1.2)

        print("\n--- capture exclusion test ---")
        print(f"  WDA_EXCLUDEFROMCAPTURE applied: {overlay.is_capture_excluded}")

        excluded_count = count_marker_pixels(capture_region(
            bounds.left, bounds.top, bounds.width, bounds.height
        ).pixels)
        print(f"  marker pixels WITH exclusion:    {excluded_count:>6}   (want 0)")

        # Control: turn it off and confirm the capture path can see the window
        # at all. Without this, a broken capture would look like a pass.
        overlay.set_capture_excluded(False)
        wait_pumping(overlay, 0.8)
        visible_count = count_marker_pixels(capture_region(
            bounds.left, bounds.top, bounds.width, bounds.height
        ).pixels)
        print(f"  marker pixels WITHOUT exclusion: {visible_count:>6}   (want > 0)")

        overlay.set_capture_excluded(True)

        print()
        if excluded_count == 0 and visible_count > 0:
            print("  PASS - the overlay is invisible to our own screenshots.")
            print("         Invariant 7 holds. The model will never see the cat.")
        elif visible_count == 0:
            print("  INCONCLUSIVE - the capture path could not see the overlay")
            print("                 even with exclusion off, so the test proves")
            print("                 nothing. Fix capture_region first.")
        else:
            print("  FAIL - the overlay appears in screenshots.")
            print("         Do not build capture on top of this. The model would")
            print("         see the cat and try to reason about it.")

        print(f"\nHolding for {args.seconds:.0f}s. The window is click-through -")
        print("try clicking through it. Ctrl+C to stop early.")
        started = time.perf_counter()
        try:
            while True:
                elapsed = time.perf_counter() - started
                if elapsed >= args.seconds:
                    break
                overlay.draw(render_circle(OVERLAY_SIZE, elapsed * 3.0))
                overlay.pump_messages()
                time.sleep(0.033)
        except KeyboardInterrupt:
            print("\nstopped.")

    print("\nOverlay closed.")


if __name__ == "__main__":
    main()
