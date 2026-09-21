"""Is the machine ready for the voice loop?

Checks what is installed and which keys are set, then says what is still
missing. Run it before 0.6 rather than discovering a missing key halfway
through a websocket handshake.

    python scripts/check_keys.py

Prints only the last four characters of any key. Terminals get screenshotted and
recorded, and a key that leaks that way is just as revoked as one committed to
git.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meow.config import ENV_PATH, describe

# LANGSMITH_API_KEY is Phase 1 tracing. Reporting it as "still needed" during
# Phase 0 trains the reader to ignore this script, which is the opposite of
# what a setup check is for.
OPTIONAL_KEYS = {"LANGSMITH_API_KEY"}

# module name -> (what needs it, which phase)
PACKAGES = [
    ("PIL", "the cat and screenshot downscaling", "0.5"),
    ("win32api", "Win32 interop (pywin32)", "0.1"),
    ("uiautomation", "UIA tree walking", "1.1"),
    ("openai", "the harness model", "0.6"),
    ("assemblyai", "streaming speech to text", "0.6"),
    ("elevenlabs", "text to speech", "0.6"),
    ("sounddevice", "microphone and playback", "0.6"),
]


def installed(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def main() -> None:
    print(f"\npython {sys.version.split()[0]}  on  {sys.platform}")
    print(f"reading keys from {ENV_PATH}"
          f"{'' if ENV_PATH.exists() else '   <- does not exist yet'}")

    print("\nPACKAGES")
    missing_packages = []
    for module_name, purpose, phase in PACKAGES:
        ok = installed(module_name)
        if not ok:
            missing_packages.append(module_name)
        mark = "ok     " if ok else "MISSING"
        print(f"  {mark}  {module_name:<14} {purpose}  (phase {phase})")

    print("\nKEYS")
    missing_keys = []
    for status in describe():
        optional = status.name in OPTIONAL_KEYS
        if not status.present and not optional:
            missing_keys.append(status)

        if status.present:
            mark = "ok     "
        else:
            mark = "later  " if optional else "not set"
        suffix = "   (phase 1, optional)" if optional and not status.present else ""
        print(f"  {mark}  {status.name:<22} {status.masked}{suffix}")
        if not status.present and not optional:
            print(f"           {status.purpose} - {status.source}")

    print()
    if not missing_packages and not missing_keys:
        print("ready. the voice loop has everything it needs.")
        return

    if missing_packages:
        voice_loop = {"openai", "assemblyai", "elevenlabs", "sounddevice"}
        blocking = [name for name in missing_packages if name not in voice_loop]
        if blocking:
            print(f"install now:   pip install {' '.join(blocking)}")
        later = [name for name in missing_packages if name in voice_loop]
        if later:
            print(f"install for 0.6:  pip install {' '.join(later)}")

    if missing_keys:
        print(f"\nstill need: {', '.join(s.name for s in missing_keys)}")
        print(f"paste them into {ENV_PATH.name} and run this again.")
        if not ENV_PATH.exists():
            print("  the file does not exist yet:  cp .env.example .env")


if __name__ == "__main__":
    main()
