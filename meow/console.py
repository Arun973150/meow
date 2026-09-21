"""Console output on Windows.

The default Windows console encoding is cp1252, which cannot represent the
characters a speech API actually returns - curly quotes, ellipses, dashes.
Printing a formatted transcript crashes with UnicodeEncodeError, and it crashes
in the print, not in the code that produced the text, so the traceback points
somewhere innocent.

Every script calls use_utf8_console() before printing anything the network
produced. errors="replace" rather than "strict": a mangled character is a far
better outcome than a dead process.
"""

from __future__ import annotations

import sys
import warnings


def use_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                # Redirected to something that will not take a new encoding.
                # Not worth failing over; the replace below still applies.
                pass


def quiet_library_warnings() -> None:
    """Kept for callers. The filtering itself lives in meow/__init__.py.

    It has to run when the package is first imported - LangGraph emits its
    notice on the way in, which is before any script body executes, so doing
    it here was always too late.
    """
