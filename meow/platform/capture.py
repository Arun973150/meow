"""Screen capture.

GDI BitBlt into a DIB section, returned as raw top-down BGRA. Deliberately the
same pixel format the overlay draws in, so a captured region and a rendered
frame can be compared without a conversion in between - which is what makes the
capture-exclusion self test possible.

CAPTUREBLT is always set. Without it BitBlt silently skips layered windows,
which would make any test of "is our layered window in this screenshot" pass for
entirely the wrong reason.

Call enable_per_monitor_dpi_awareness() before anything here, or the rectangle
requested is not the rectangle captured. See dpi.py.
"""

from __future__ import annotations

import ctypes
import io as _io
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image

from .monitors import Monitor, get_cursor_position, get_virtual_desktop

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


# Every GDI handle needs an explicit restype on 64-bit Python. Unset, ctypes
# assumes a C int and truncates the pointer, which surfaces as
# "OverflowError: int too long to convert" several calls later, in innocent code.
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.BitBlt.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
]


@dataclass(frozen=True)
class Capture:
    """Raw top-down BGRA pixels and the rectangle they came from."""

    pixels: bytes
    left: int
    top: int
    width: int
    height: int


def capture_region(left: int, top: int, width: int, height: int) -> Capture:
    """Screenshot a rectangle of the virtual desktop.

    Coordinates are virtual desktop coordinates, so they may be negative for a
    monitor placed left of or above the primary one.
    """
    screen_device_context = user32.GetDC(None)
    memory_device_context = gdi32.CreateCompatibleDC(screen_device_context)
    bitmap = gdi32.CreateCompatibleBitmap(screen_device_context, width, height)
    previous = gdi32.SelectObject(memory_device_context, bitmap)

    gdi32.BitBlt(
        memory_device_context, 0, 0, width, height,
        screen_device_context, left, top, SRCCOPY | CAPTUREBLT,
    )

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height  # negative means top-down rows
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = BI_RGB

    info = BITMAPINFO()
    info.bmiHeader = header

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(
        memory_device_context, bitmap, 0, height, buffer,
        ctypes.byref(info), DIB_RGB_COLORS,
    )

    gdi32.SelectObject(memory_device_context, previous)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_device_context)
    user32.ReleaseDC(None, screen_device_context)

    return Capture(buffer.raw, left, top, width, height)


def mean_luminance(capture: Capture, sample_stride: int = 7) -> float:
    """Average perceived brightness of a capture, 0.0 (black) to 1.0 (white).

    Samples every Nth pixel rather than all of them. The cat only needs to know
    whether it is sitting on something light or dark, and a full pass over a
    region costs more than the answer is worth at 30fps.

    Uses Rec. 601 weights: the eye is far more sensitive to green than to blue,
    so a flat channel average calls a saturated blue background much lighter
    than it looks.
    """
    pixels = capture.pixels
    step = 4 * max(1, sample_stride)
    total = 0.0
    count = 0
    for index in range(0, len(pixels) - 3, step):
        blue, green, red = pixels[index], pixels[index + 1], pixels[index + 2]
        total += 0.114 * blue + 0.587 * green + 0.299 * red
        count += 1
    if count == 0:
        return 1.0
    return total / count / 255.0


# --- what the model actually receives --------------------------------------

# Clicky's number, and a good one. Above this the token cost rises faster than
# the model's ability to use the detail: a screenshot runs 1,000-1,800 tokens
# regardless, and a 4K frame mostly buys empty desktop.
MAX_EDGE_PIXELS = 1280

# Quality 80 is where JPEG stops being visibly lossy on UI text. Lower and
# small labels start to smear, which matters when the model is reading buttons.
JPEG_QUALITY = 80


@dataclass(frozen=True)
class ScreenShot:
    """One monitor, downscaled, ready to send.

    Carries `scale` because the model answers in IMAGE pixels and every click
    happens in SCREEN pixels. Losing that number is how a pointing system ends
    up off by the downscale factor on every monitor but one.
    """

    label: str
    monitor: Monitor
    image: Image.Image
    scale: float  # image pixels -> screen pixels is a division by this

    @property
    def is_cursor_screen(self) -> bool:
        cursor_x, cursor_y = get_cursor_position()
        return self.monitor.contains(cursor_x, cursor_y)

    def to_screen(self, image_x: float, image_y: float) -> tuple[int, int]:
        """Map a point the model gave us back to virtual desktop coordinates.

        This is the return leg of the POINT protocol in 06-prior-art.md. The
        monitor origin has to be added back because a screenshot always starts
        at (0, 0) while the monitor it came from may not - it can be negative.
        """
        return (
            int(round(self.monitor.left + image_x / self.scale)),
            int(round(self.monitor.top + image_y / self.scale)),
        )

    def to_image(self, screen_x: float, screen_y: float) -> tuple[int, int]:
        """The other direction, for drawing a screen position onto the image."""
        return (
            int(round((screen_x - self.monitor.left) * self.scale)),
            int(round((screen_y - self.monitor.top) * self.scale)),
        )

    def to_jpeg(self, quality: int = JPEG_QUALITY) -> bytes:
        buffer = _io.BytesIO()
        # RGB, not RGBA: JPEG has no alpha channel and Pillow raises rather
        # than dropping it silently.
        self.image.convert("RGB").save(buffer, format="JPEG", quality=quality)
        return buffer.getvalue()

    def __repr__(self) -> str:
        return (f"<ScreenShot {self.label} {self.image.width}x{self.image.height} "
                f"from {self.monitor.width}x{self.monitor.height} "
                f"scale={self.scale:.3f}>")


def bgra_to_image(capture: Capture) -> Image.Image:
    """Raw BGRA bytes to a Pillow image, without a per-pixel Python loop."""
    return Image.frombuffer(
        "RGBA", (capture.width, capture.height), capture.pixels,
        "raw", "BGRA", 0, 1,
    )


def capture_screens(max_edge: int = MAX_EDGE_PIXELS) -> list[ScreenShot]:
    """Every monitor, downscaled, **cursor's screen first**.

    Ordering is not cosmetic. The model weights the first image it is given, and
    the screen the user is pointing at is almost always the one they are talking
    about. Clicky sorts the same way for the same reason.

    Labels are `screen1`, `screen2` in the order returned, so a reply tagged
    `[POINT:x,y:label:screen2]` resolves against this list directly.
    """
    desktop = get_virtual_desktop()
    cursor_x, cursor_y = get_cursor_position()

    ordered = sorted(
        desktop.monitors,
        key=lambda monitor: (
            not monitor.contains(cursor_x, cursor_y),  # cursor screen first
            not monitor.is_primary,                    # then primary
            monitor.left,                              # then left to right
        ),
    )

    shots: list[ScreenShot] = []
    for index, monitor in enumerate(ordered, start=1):
        raw = capture_region(monitor.left, monitor.top,
                             monitor.width, monitor.height)
        image = bgra_to_image(raw)

        longest_edge = max(monitor.width, monitor.height)
        scale = min(1.0, max_edge / longest_edge)
        if scale < 1.0:
            image = image.resize(
                (max(1, round(monitor.width * scale)),
                 max(1, round(monitor.height * scale))),
                Image.LANCZOS,
            )

        shots.append(ScreenShot(f"screen{index}", monitor, image, scale))
    return shots
