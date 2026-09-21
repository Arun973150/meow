"""Monitor geometry and the virtual desktop.

Windows arranges all monitors into one coordinate plane called the virtual
desktop. The primary monitor's top-left is the origin, which means any monitor
placed to the left of or above it has NEGATIVE coordinates. Code that assumes
(0, 0) is the top-left of everything works fine on a single-monitor machine and
then silently misplaces every window and every click on a multi-monitor one.

Call enable_per_monitor_dpi_awareness() before anything in this module, or the
numbers come back virtualized. See dpi.py.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

MONITORINFOF_PRIMARY = 0x00000001


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMONITOR,
    wintypes.HDC,
    ctypes.POINTER(RECT),
    wintypes.LPARAM,
)


@dataclass(frozen=True)
class Monitor:
    """One physical display, in virtual desktop coordinates."""

    handle: int
    device_name: str
    is_primary: bool
    left: int
    top: int
    right: int
    bottom: int
    # The work area excludes the taskbar. Useful for placing UI, not for
    # capture - a screenshot covers the full monitor rect.
    work_left: int
    work_top: int
    work_right: int
    work_bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def __repr__(self) -> str:
        primary_marker = " PRIMARY" if self.is_primary else ""
        return (
            f"<Monitor {self.device_name}{primary_marker} "
            f"{self.width}x{self.height} at ({self.left},{self.top})>"
        )


@dataclass(frozen=True)
class VirtualDesktop:
    """The bounding box of every monitor combined.

    An overlay that must cover all screens is exactly this rectangle, and its
    origin is frequently negative.
    """

    left: int
    top: int
    width: int
    height: int
    monitors: tuple[Monitor, ...]

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def has_negative_origin(self) -> bool:
        return self.left < 0 or self.top < 0

    def monitor_at(self, x: int, y: int) -> Monitor | None:
        for monitor in self.monitors:
            if monitor.contains(x, y):
                return monitor
        return None

    @property
    def primary(self) -> Monitor | None:
        for monitor in self.monitors:
            if monitor.is_primary:
                return monitor
        return self.monitors[0] if self.monitors else None

    def __repr__(self) -> str:
        return (
            f"<VirtualDesktop {self.width}x{self.height} at "
            f"({self.left},{self.top}) across {len(self.monitors)} monitor(s)>"
        )


def enumerate_monitors() -> tuple[Monitor, ...]:
    """Every attached display, in whatever order Windows reports them."""
    found: list[Monitor] = []

    def on_monitor(monitor_handle, _device_context, _rect, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if not user32.GetMonitorInfoW(monitor_handle, ctypes.byref(info)):
            return True  # keep enumerating; one bad monitor is not fatal

        found.append(
            Monitor(
                handle=int(monitor_handle),
                device_name=info.szDevice,
                is_primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                left=info.rcMonitor.left,
                top=info.rcMonitor.top,
                right=info.rcMonitor.right,
                bottom=info.rcMonitor.bottom,
                work_left=info.rcWork.left,
                work_top=info.rcWork.top,
                work_right=info.rcWork.right,
                work_bottom=info.rcWork.bottom,
            )
        )
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(on_monitor), 0)

    # Primary first, then left-to-right. Clicky sorts the cursor's screen first
    # when captioning screenshots for the model; this is the stable base order
    # that sort is applied to.
    return tuple(sorted(found, key=lambda m: (not m.is_primary, m.left, m.top)))


def get_virtual_desktop() -> VirtualDesktop:
    """The full bounding box across all monitors, plus the monitors in it."""
    return VirtualDesktop(
        left=user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        top=user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        width=user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        height=user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
        monitors=enumerate_monitors(),
    )


def get_cursor_position() -> tuple[int, int]:
    """Cursor in virtual desktop coordinates.

    Only returns physical pixels if DPI awareness was set first.
    """
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y
