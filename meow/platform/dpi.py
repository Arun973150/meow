"""DPI awareness.

This has to run before the process creates any window, and before it asks
Windows about any coordinate. It is the first line of the program.

Without PER_MONITOR_AWARE_V2, Windows silently virtualizes coordinates for a
DPI-unaware process: GetCursorPos, GetSystemMetrics and SendInput all report and
accept numbers in a scaled coordinate space that does not match physical pixels
on any monitor whose scale differs from the primary one. Nothing errors. The
clicks just land somewhere else, and every layer above inherits the bug.

See AGENTS.md invariant 8.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

# The DPI_AWARENESS_CONTEXT values are pseudo-handles, not integers, so they
# have to be passed as void pointers rather than ints.
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = ctypes.c_void_p(-3)

# Legacy shcore values, used only on Windows 8.1 through early Windows 10.
PROCESS_PER_MONITOR_DPI_AWARE = 2


class DpiAwarenessResult:
    """What we managed to set, so startup can log it rather than assume it."""

    def __init__(self, applied: str, coordinates_are_physical: bool) -> None:
        self.applied = applied
        self.coordinates_are_physical = coordinates_are_physical

    def __repr__(self) -> str:
        return (
            f"DpiAwarenessResult(applied={self.applied!r}, "
            f"coordinates_are_physical={self.coordinates_are_physical})"
        )


def enable_per_monitor_dpi_awareness() -> DpiAwarenessResult:
    """Make this process report and accept real physical pixels.

    Tries the modern API first and falls back through two older ones. The
    fallbacks are worse - PER_MONITOR_AWARE (v1) does not rescale non-client
    areas on DPI change - but all three give physical coordinates, which is the
    part that matters for clicking.
    """
    # Windows 10 1703+. The only version that handles DPI changes correctly for
    # a window spanning monitors of different scales, which our overlay does.
    try:
        if user32.SetProcessDpiAwarenessContext(
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ):
            return DpiAwarenessResult("PER_MONITOR_AWARE_V2", True)
    except AttributeError:
        pass  # older than Windows 10 1703

    # Windows 8.1+.
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        # Returns an HRESULT: S_OK is 0, and E_ACCESSDENIED means awareness was
        # already set (often by a manifest), which is not a failure for us.
        result = shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
        if result in (0, 0x80070005):
            return DpiAwarenessResult("PER_MONITOR_AWARE (shcore)", True)
    except (OSError, AttributeError):
        pass

    # Vista+. System-wide awareness only: correct on the primary monitor,
    # virtualized on any monitor with a different scale.
    try:
        if user32.SetProcessDPIAware():
            return DpiAwarenessResult("SYSTEM_AWARE (legacy)", True)
    except AttributeError:
        pass

    # Nothing worked. Say so loudly rather than let the caller assume clicks
    # will land where it asked.
    return DpiAwarenessResult("NONE - coordinates are virtualized", False)


def get_dpi_for_window(window_handle: int) -> int:
    """DPI of the monitor this window is on. 96 is 100% scaling."""
    try:
        return int(user32.GetDpiForWindow(wintypes.HWND(window_handle)))
    except AttributeError:
        return 96  # pre-1607; assume unscaled rather than guess


def scale_factor_for_window(window_handle: int) -> float:
    """1.0 at 100%, 1.5 at 150%, and so on."""
    return get_dpi_for_window(window_handle) / 96.0
