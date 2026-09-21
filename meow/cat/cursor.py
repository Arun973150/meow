"""The cat cursor.

While the cat is travelling to something it found, the mouse pointer becomes a
cat. This is the idea the whole project started from - the cursor IS the
companion - and it is the moment the user sees Meow act rather than talk.

Two halves here, and the second matters more than it looks.

**Turning the artwork into a cursor.** `cat_cursor.png` is a drawing on a black
background with a glow around it. The cat is white and its outline is black -
the same black as the background - so the shape cannot be cut out by colour
alone. What works: find the white fill by brightness, grow that mask outward by
the thickness of the outline, and use the result as the alpha channel. The glow
is dimmer than the fill and further away than the outline, so it falls outside.

**Putting it back.** `SetSystemCursor` replaces the arrow for the WHOLE DESKTOP,
for every application, until something puts it back. If this process dies with
the cat installed, the user is left with a cat cursor and no idea why. Restore
is therefore wired three ways - context manager, `atexit`, and a SIGINT handler -
the same belt-and-braces the screen-reader flag spike used, and for the same
reason.
"""

from __future__ import annotations

import atexit
import ctypes
import signal
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageFilter

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# The standard arrow. Replacing this one is what makes the change visible
# everywhere rather than only over our own window.
OCR_NORMAL = 32512
SPI_SETCURSORS = 0x0057
SPIF_SENDCHANGE = 0x0002

# Match the system cursor exactly. Windows reports the size it expects through
# GetSystemMetrics, and anything larger is a cat that looms over the desktop
# rather than a pointer. Resolved at import so a 150% display gets 48 and a
# 100% display gets 32, without either being hardcoded.
SM_CXCURSOR = 13


def _system_cursor_size() -> int:
    size = ctypes.windll.user32.GetSystemMetrics(SM_CXCURSOR)
    return size if size > 0 else 32


DEFAULT_CURSOR_SIZE = _system_cursor_size()

SOURCE_IMAGE = Path(__file__).resolve().parent.parent.parent / "cat_cursor.png"


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


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


user32.CreateIconIndirect.restype = wintypes.HICON
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.SetSystemCursor.argtypes = [wintypes.HICON, wintypes.DWORD]
user32.SetSystemCursor.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.CopyIcon.restype = wintypes.HICON
user32.CopyIcon.argtypes = [wintypes.HICON]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
gdi32.CreateBitmap.restype = wintypes.HBITMAP
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]


def extract_cursor_art(path: Path | None = None,
                       size: int = DEFAULT_CURSOR_SIZE) -> Image.Image:
    """Cut the cat out of the source drawing and return it as RGBA.

    The outline is the same black as the background, so a colour key cannot
    separate them. Instead the white fill is found by brightness and grown
    outward by roughly the outline thickness; the glow sits further out than
    that and is left behind.
    """
    source = Image.open(path or SOURCE_IMAGE).convert("RGB")

    brightness = source.convert("L")
    fill = brightness.point(lambda value: 255 if value > 170 else 0)

    # Grow the fill to swallow the outline. MaxFilter of size N grows by N//2
    # pixels; 13 covers the ~6px outline in this artwork at full resolution.
    outline_reach = 13
    mask = fill.filter(ImageFilter.MaxFilter(outline_reach))

    box = mask.getbbox()
    if box is None:
        raise ValueError(f"no cursor shape found in {path or SOURCE_IMAGE}")

    cropped = source.crop(box)
    cropped_mask = mask.crop(box)

    art = Image.new("RGBA", cropped.size)
    art.paste(cropped, (0, 0))
    art.putalpha(cropped_mask)

    # Square it off before scaling, so Windows does not stretch the cat.
    side = max(art.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(art, ((side - art.width) // 2, 0), art)
    return square.resize((size, size), Image.LANCZOS)


def _hbitmap_from_rgba(image: Image.Image) -> wintypes.HBITMAP:
    """A 32-bit top-down DIB holding the image, premultiplied."""
    width, height = image.size
    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height  # top-down
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0

    info = BITMAPINFO()
    info.bmiHeader = header

    screen_dc = user32.GetDC(None)
    pixel_pointer = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(
        screen_dc, ctypes.byref(info), 0, ctypes.byref(pixel_pointer), None, 0)
    user32.ReleaseDC(None, screen_dc)

    if not bitmap:
        raise ctypes.WinError(ctypes.get_last_error())

    from .sprite import rgba_to_premultiplied_bgra
    pixels = bytes(rgba_to_premultiplied_bgra(image))
    ctypes.memmove(pixel_pointer, pixels, len(pixels))
    return bitmap


def build_cursor(image: Image.Image,
                 hotspot: tuple[int, int] | None = None) -> wintypes.HICON:
    """Turn an RGBA image into a Windows cursor handle.

    The hotspot is the pixel that actually points - the tip of the arrow, not
    the middle of the picture. Getting it wrong means every click lands a
    couple of dozen pixels away from where the user aimed.
    """
    width, height = image.size
    if hotspot is None:
        # The arrow tip is the topmost opaque pixel in this artwork.
        alpha = image.getchannel("A")
        box = alpha.getbbox()
        hotspot = ((box[0] + box[2]) // 2 if box else 0, box[1] if box else 0)
        # Nudge to the actual point: the tip is the leftmost opaque pixel on
        # the topmost opaque row.
        top_row = box[1] if box else 0
        for x in range(width):
            if alpha.getpixel((x, top_row)) > 0:
                hotspot = (x, top_row)
                break

    colour_bitmap = _hbitmap_from_rgba(image)
    # An all-zero mask: with a 32-bit colour bitmap the alpha channel does the
    # masking, and a non-empty mask would punch holes in it.
    mask_bitmap = gdi32.CreateBitmap(width, height, 1, 1,
                                     bytes((width * height) // 8 or 1))

    info = ICONINFO()
    info.fIcon = False  # False means cursor, True means icon
    info.xHotspot = hotspot[0]
    info.yHotspot = hotspot[1]
    info.hbmMask = mask_bitmap
    info.hbmColor = colour_bitmap

    handle = user32.CreateIconIndirect(ctypes.byref(info))
    gdi32.DeleteObject(colour_bitmap)
    gdi32.DeleteObject(mask_bitmap)

    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def restore_system_cursors() -> None:
    """Put every system cursor back the way the user had it."""
    user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, SPIF_SENDCHANGE)


class CatCursor:
    """Swaps the system arrow for the cat, and is careful about putting it back.

    `SetSystemCursor` is desktop-wide and persists after the process exits. A
    crash while installed leaves the user with a cat cursor and nothing to
    explain it, so restore is registered three ways: the context manager, an
    atexit hook, and a SIGINT handler.
    """

    def __init__(self, size: int = DEFAULT_CURSOR_SIZE,
                 path: Path | None = None) -> None:
        self.image = extract_cursor_art(path, size)
        self._handle: wintypes.HICON | None = None
        self._installed = False
        self._atexit_registered = False
        self.hotspot: tuple[int, int] = (0, 0)

    @property
    def installed(self) -> bool:
        return self._installed

    def install(self) -> None:
        if self._installed:
            return

        handle = build_cursor(self.image)

        # SetSystemCursor TAKES OWNERSHIP of the handle it is given and destroys
        # it on replacement. Passing the same handle twice is a use-after-free,
        # so a copy goes to Windows and the original stays ours.
        if not user32.SetSystemCursor(user32.CopyIcon(handle), OCR_NORMAL):
            user32.DestroyIcon(handle)
            raise ctypes.WinError(ctypes.get_last_error())

        self._handle = handle
        self._installed = True

        if not self._atexit_registered:
            atexit.register(self.remove)
            previous = signal.getsignal(signal.SIGINT)

            def on_interrupt(signum, frame):
                self.remove()
                if callable(previous):
                    previous(signum, frame)
                else:
                    raise KeyboardInterrupt

            try:
                signal.signal(signal.SIGINT, on_interrupt)
            except ValueError:
                pass  # not on the main thread; atexit still covers it
            self._atexit_registered = True

    def remove(self) -> None:
        if not self._installed:
            return
        restore_system_cursors()
        if self._handle:
            user32.DestroyIcon(self._handle)
            self._handle = None
        self._installed = False

    def __enter__(self) -> "CatCursor":
        self.install()
        return self

    def __exit__(self, *_exc_info) -> None:
        self.remove()
