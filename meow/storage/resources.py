"""Finding the files that ship WITH the app, however it was installed.

Three layouts, and a path that works in one is wrong in the others:

```
a git clone        meow/cat/cursor.py reads ../../cat_cursor.png
a pip install      the same file is in site-packages, and only if
                   package-data lists it
a frozen bundle    there is no .py file at all - PyInstaller unpacks
                   everything to sys._MEIPASS and __file__ points inside it
```

`Path(__file__).parent.parent.parent / "cat_cursor.png"` is right for the
first and wrong for both the others, which is the state this project was in:
the cat cursor was outside the package and **not in `package-data`**, so a
`pip install meow` has never had one. Nothing failed - `CatCursor` reports
that the file is missing and the pointer simply stays normal.

⚠ **A missing resource must stay a MISSING RESOURCE, not an exception.**
Every caller here already copes with absence: no cursor image means the
normal pointer, no `winocr.ps1` means no OCR. A packaging mistake that
crashes the app is worse than one that quietly costs a feature - but a
packaging mistake nobody can see is worse still, which is why `missing()`
exists and `meow doctor` prints it.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Everything the app needs at runtime that is not Python, as a path relative
# to the package root. Listed in ONE place so `meow doctor`, the PyInstaller
# spec and `package-data` cannot drift apart - which is exactly how the
# recipes vanished once, and how the cursor has been missing from every
# wheel ever built.
SHIPPED = (
    "assets/cat_cursor.png",
    "desktop/winocr.ps1",
)

# The recipes are a folder rather than a file, so they are checked by
# counting rather than by name.
SHIPPED_FOLDERS = ("recipes",)


def frozen() -> bool:
    """Is this a PyInstaller bundle rather than a source tree?"""
    return bool(getattr(sys, "frozen", False)
                and hasattr(sys, "_MEIPASS"))


def package_root() -> Path:
    """The folder the package's own files sit in.

    Frozen, PyInstaller unpacks the data tree into `sys._MEIPASS` and the
    spec puts the package's files under a `meow/` folder inside it, so the
    two layouts agree and nothing downstream has to know which it is.
    """
    if frozen():
        return Path(sys._MEIPASS) / "meow"  # noqa: SLF001 - PyInstaller's own
    return Path(__file__).resolve().parent.parent


def resource(*parts: str) -> Path:
    """Where a shipped file is. May not exist; callers check."""
    return package_root().joinpath(*parts)


def missing() -> list[str]:
    """What should have shipped and did not.

    Read by `meow doctor`, because a resource that is absent costs a feature
    silently: a shelf with nothing on it looks exactly like a request that
    matched nothing.
    """
    absent = [name for name in SHIPPED if not resource(name).exists()]
    for folder in SHIPPED_FOLDERS:
        place = resource(folder)
        if not place.is_dir() or not any(place.glob("*.md")):
            absent.append(folder + "/")
    return absent
