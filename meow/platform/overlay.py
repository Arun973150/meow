"""The overlay window the cat lives in.

Four properties, and the whole rest of the project depends on all four:

  layered      per-pixel alpha, so the cat has soft edges over whatever is
               behind it instead of sitting in a grey box
  transparent  clicks pass straight through to the app underneath. The user
               keeps using their machine while the cat is on screen
  topmost      stays above other windows without stealing focus
  no-activate  clicking near it never takes focus away from what the user is
               doing, and it never appears in alt-tab

And one more that is easy to forget and expensive to debug: the window is
EXCLUDED FROM CAPTURE. Meow screenshots the desktop and sends it to a model. If
the overlay is in those screenshots, the model sees a cat pointing at things and
tries to reason about it. See AGENTS.md invariants 7 and 8.

Drawing is per-pixel alpha via UpdateLayeredWindow, which needs premultiplied
BGRA. That is not the same as ordinary RGBA and getting it wrong produces
bright halos around every edge - see premultiply().
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, replace

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- window styles -------------------------------------------------------

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020      # click-through
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000       # never take focus
WS_EX_TOOLWINDOW = 0x00000080       # keep out of alt-tab and the taskbar

# --- capture exclusion ---------------------------------------------------

WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
# Windows 10 version 2004 and later. DWM leaves the window out of every capture
# surface, including our own screenshots. On older builds the call fails and the
# only alternative is WDA_MONITOR, which blacks the window out for the user too.
WDA_EXCLUDEFROMCAPTURE = 0x00000011

# --- layered window drawing ---------------------------------------------

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0

SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

WM_DESTROY = 0x0002
PM_REMOVE = 0x0001


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

# --- ctypes signatures ---------------------------------------------------
#
# These are not optional on 64-bit Python. A handle is a 64-bit pointer, but
# ctypes assumes a C int return (32 bits) unless told otherwise, so an unset
# restype silently truncates every HDC and HBITMAP. The symptom is not a wrong
# picture, it is "OverflowError: int too long to convert" several calls later,
# pointing at innocent code.

user32.DefWindowProcW.restype = ctypes.c_longlong
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
user32.GetWindowDisplayAffinity.argtypes = [
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
]
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
    wintypes.HDC, ctypes.POINTER(POINT), wintypes.COLORREF,
    ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
]
user32.UpdateLayeredWindow.restype = wintypes.BOOL

gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]


_WINDOW_CLASS_NAME = "MeowOverlayWindow"
_registered_class_atom: int | None = None
# The wndproc must outlive the window. If Python garbage-collects the callback,
# Windows calls into freed memory and the process dies without a traceback.
_window_procedure_reference: WNDPROC | None = None


def _register_window_class() -> int:
    global _registered_class_atom, _window_procedure_reference
    if _registered_class_atom is not None:
        return _registered_class_atom

    def window_procedure(window_handle, message, wparam, lparam):
        if message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(window_handle, message, wparam, lparam)

    _window_procedure_reference = WNDPROC(window_procedure)

    window_class = WNDCLASSEXW()
    window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
    window_class.style = 0
    window_class.lpfnWndProc = ctypes.cast(_window_procedure_reference, ctypes.c_void_p)
    window_class.cbClsExtra = 0
    window_class.cbWndExtra = 0
    window_class.hInstance = kernel32.GetModuleHandleW(None)
    window_class.hIcon = None
    window_class.hCursor = None
    window_class.hbrBackground = None
    window_class.lpszMenuName = None
    window_class.lpszClassName = _WINDOW_CLASS_NAME
    window_class.hIconSm = None

    atom = user32.RegisterClassExW(ctypes.byref(window_class))
    if not atom:
        raise ctypes.WinError(ctypes.get_last_error())
    _registered_class_atom = atom
    return atom


@dataclass(frozen=True)
class Bounds:
    """Window position and size in virtual desktop coordinates."""

    left: int
    top: int
    width: int
    height: int


class CaptureExclusionUnavailable(RuntimeError):
    """SetWindowDisplayAffinity refused WDA_EXCLUDEFROMCAPTURE.

    Raised rather than swallowed. Continuing silently means the cat ends up in
    every screenshot sent to the model, which produces confusing behaviour far
    away from the actual cause.
    """


class Overlay:
    """A click-through, always-on-top, capture-excluded layered window."""

    def __init__(self, bounds: Bounds, exclude_from_capture: bool = True,
                 click_through: bool = True) -> None:
        self.bounds = bounds
        _register_window_class()

        # WS_EX_TRANSPARENT is what makes the cat click-through, and it is
        # right for the cat: it sits over the user's work and must never
        # intercept a click meant for what is underneath. An overlay that is
        # meant to BE clicked - an agent's icon - has to leave it off, or the
        # click passes straight through to whatever is behind it and the icon
        # is decoration.
        #
        # WS_EX_NOACTIVATE stays either way. Taking focus would pull it away
        # from whatever the user is typing in, which is worse than any benefit.
        style = (WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE
                 | WS_EX_TOOLWINDOW)
        if click_through:
            style |= WS_EX_TRANSPARENT

        self.click_through = click_through
        self.handle = user32.CreateWindowExW(
            style,
            _WINDOW_CLASS_NAME,
            "Meow",
            WS_POPUP,
            bounds.left,
            bounds.top,
            bounds.width,
            bounds.height,
            None,
            None,
            kernel32.GetModuleHandleW(None),
            None,
        )
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

        self._closed = False
        if exclude_from_capture:
            self.set_capture_excluded(True)

    # --- capture exclusion ----------------------------------------------

    def set_capture_excluded(self, excluded: bool) -> None:
        """Hide this window from every screen capture, including our own."""
        affinity = WDA_EXCLUDEFROMCAPTURE if excluded else WDA_NONE
        if not user32.SetWindowDisplayAffinity(self.handle, affinity):
            error = ctypes.get_last_error()
            if excluded:
                raise CaptureExclusionUnavailable(
                    "SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) failed with "
                    f"error {error}. This needs Windows 10 version 2004 or later. "
                    "Without it the overlay appears in Meow's own screenshots."
                )
            raise ctypes.WinError(error)

    @property
    def is_capture_excluded(self) -> bool:
        affinity = wintypes.DWORD()
        if not user32.GetWindowDisplayAffinity(self.handle, ctypes.byref(affinity)):
            return False
        return affinity.value == WDA_EXCLUDEFROMCAPTURE

    # --- drawing ---------------------------------------------------------

    def draw(self, premultiplied_bgra: bytes | bytearray) -> None:
        """Paint the window from a top-down premultiplied BGRA buffer.

        The buffer must be exactly width * height * 4 bytes. UpdateLayeredWindow
        redraws and repositions in one call, which is why there is no separate
        invalidate/paint cycle here.
        """
        expected = self.bounds.width * self.bounds.height * 4
        if len(premultiplied_bgra) != expected:
            raise ValueError(
                f"expected {expected} bytes for "
                f"{self.bounds.width}x{self.bounds.height}, "
                f"got {len(premultiplied_bgra)}"
            )

        screen_device_context = user32.GetDC(None)
        memory_device_context = gdi32.CreateCompatibleDC(screen_device_context)

        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = self.bounds.width
        # Negative height means top-down rows. DIBs are bottom-up by default,
        # and a positive value here silently renders the cat upside down.
        header.biHeight = -self.bounds.height
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = BI_RGB

        bitmap_info = BITMAPINFO()
        bitmap_info.bmiHeader = header

        pixel_pointer = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            memory_device_context,
            ctypes.byref(bitmap_info),
            DIB_RGB_COLORS,
            ctypes.byref(pixel_pointer),
            None,
            0,
        )
        if not bitmap:
            gdi32.DeleteDC(memory_device_context)
            user32.ReleaseDC(None, screen_device_context)
            raise ctypes.WinError(ctypes.get_last_error())

        try:
            ctypes.memmove(
                pixel_pointer, bytes(premultiplied_bgra), expected
            )
            previous_bitmap = gdi32.SelectObject(memory_device_context, bitmap)

            window_position = POINT(self.bounds.left, self.bounds.top)
            window_size = SIZE(self.bounds.width, self.bounds.height)
            source_position = POINT(0, 0)

            blend = BLENDFUNCTION(
                BlendOp=AC_SRC_OVER,
                BlendFlags=0,
                SourceConstantAlpha=255,
                AlphaFormat=AC_SRC_ALPHA,
            )

            ok = user32.UpdateLayeredWindow(
                self.handle,
                screen_device_context,
                ctypes.byref(window_position),
                ctypes.byref(window_size),
                memory_device_context,
                ctypes.byref(source_position),
                0,
                ctypes.byref(blend),
                ULW_ALPHA,
            )
            gdi32.SelectObject(memory_device_context, previous_bitmap)
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memory_device_context)
            user32.ReleaseDC(None, screen_device_context)

    # --- position --------------------------------------------------------

    def set_bounds(self, bounds: Bounds) -> None:
        """Move and resize together.

        Safe to change size every frame: draw() builds a fresh DIB section each
        call, so there is no cached surface to invalidate. The speech bubble
        relies on this - it is sized to its text, so it changes shape whenever
        the text does.
        """
        self.bounds = bounds

    def move_to(self, left: int, top: int) -> None:
        """Reposition the window.

        Takes effect on the next draw(), because UpdateLayeredWindow moves and
        repaints in a single call. Calling SetWindowPos here as well would move
        the old pixels first and then redraw them, which tears visibly on a
        sprite that moves every frame.
        """
        self.bounds = replace(self.bounds, left=int(left), top=int(top))

    # --- lifecycle -------------------------------------------------------

    def show(self) -> None:
        user32.ShowWindow(self.handle, SW_SHOWNOACTIVATE)
        # Re-assert topmost. Other applications promote themselves over time,
        # and a cat that drifts behind the active window is not much of a cat.
        user32.SetWindowPos(
            self.handle, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def hide(self) -> None:
        user32.ShowWindow(self.handle, SW_HIDE)

    def close(self) -> None:
        if not self._closed:
            user32.DestroyWindow(self.handle)
            self._closed = True

    def __enter__(self) -> "Overlay":
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()

    @staticmethod
    def pump_messages() -> None:
        """Drain pending Windows messages without blocking.

        The overlay never blocks on GetMessage: the voice loop and the agent run
        on the same clock, so the caller owns the loop and calls this.
        """
        message = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))


def premultiply(straight_bgra: bytearray) -> bytearray:
    """Convert straight-alpha BGRA to the premultiplied form GDI expects.

    Every colour channel is scaled by its own alpha. Skipping this makes
    semi-transparent pixels far too bright, which shows up as a glowing halo
    around anything with a soft edge - the classic layered-window bug.
    """
    for index in range(0, len(straight_bgra), 4):
        alpha = straight_bgra[index + 3]
        if alpha == 255:
            continue
        straight_bgra[index] = (straight_bgra[index] * alpha) // 255
        straight_bgra[index + 1] = (straight_bgra[index + 1] * alpha) // 255
        straight_bgra[index + 2] = (straight_bgra[index + 2] * alpha) // 255
    return straight_bgra
