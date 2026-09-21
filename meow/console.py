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
    """Silence warnings from libraries that the user cannot act on.

    LangGraph prints a pending-deprecation notice about a serializer default
    on every single import. It is aimed at whoever maintains this code, not at
    someone talking to a cat, and printing it above the prompt every launch
    trains people to ignore the terminal - which is where the things that DO
    matter appear.

    Only the categories, and only from libraries. Nothing here hides a warning
    raised by Meow.
    """
    for category in ("LangChainPendingDeprecationWarning",
                     "LangChainBetaWarning"):
        try:
            from langchain_core._api import beta, deprecation  # noqa: F401
        except Exception:  # noqa: BLE001
            break
    warnings.filterwarnings("ignore", category=DeprecationWarning,
                            module=r"langgraph\..*")
    warnings.filterwarnings("ignore", category=PendingDeprecationWarning)
    warnings.filterwarnings("ignore", message=r".*allowed_objects.*")
    warnings.filterwarnings("ignore", message=r".*is in beta.*")
