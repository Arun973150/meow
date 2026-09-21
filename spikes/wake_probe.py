"""
Wake Probe - Follow-up Spike
============================

`uia_probe.py` found an asymmetry worth chasing:

    Code.exe     SKELETAL    6 actionable elements
    chrome.exe   RICH      161 actionable elements

Same engine, opposite outcome. Chrome exposes its tree when a UIA client
attaches; VS Code does not. Electron's screen-reader detection has been
broken since v37 (accessibilitySupportEnabled reports false even when a
screen reader is present), so VS Code never notices a client is there.

The mechanism real screen readers use is a system-wide flag:

    SPI_SETSCREENREADER

NVDA and JAWS set it on startup and clear it on exit. Chromium has explicit
special-case handling for it. This spike sets it, re-probes, and restores it,
measuring the delta per app.

If Electron apps wake, this is THE technique - and it is an honest one to use.
Meow *is* assistive technology; this flag is exactly what it exists for.

    python spikes/wake_probe.py                    # probe every window
    python spikes/wake_probe.py --match code,slack # only these processes

SAFETY
------
The flag is system-wide and affects other running apps (some simplify
animations or change rendering while it is set). It is ALWAYS restored - on
normal exit, on Ctrl+C, and on crash. Verify with --check afterwards.
"""

from __future__ import annotations

import argparse
import atexit
import ctypes
import signal
import sys
import time
from ctypes import wintypes

try:
    import uiautomation as auto
except ImportError:
    raise SystemExit("uiautomation is not installed.\n\n    pip install uiautomation\n")

from uia_probe import probe_window, Probe, _describe_skipped  # reuse the walker


SPI_GETSCREENREADER = 0x0046
SPI_SETSCREENREADER = 0x0047
SPIF_SENDCHANGE = 0x0002

user32 = ctypes.windll.user32

# How long to let apps react to the broadcast before re-probing. Chromium
# builds its tree lazily and asynchronously; too short a wait reads a
# half-built tree and understates the effect.
SETTLE_SECONDS = 4.0

_original_state: bool | None = None


def get_screen_reader_flag() -> bool:
    value = wintypes.BOOL()
    ok = user32.SystemParametersInfoW(
        SPI_GETSCREENREADER, 0, ctypes.byref(value), 0
    )
    if not ok:
        raise OSError("SystemParametersInfoW(SPI_GETSCREENREADER) failed")
    return bool(value.value)


def set_screen_reader_flag(enabled: bool) -> None:
    """Set the system-wide screen-reader flag and broadcast the change.

    SPIF_SENDCHANGE is what makes other processes notice - without it the
    value changes but nothing is told to re-read it.
    """
    ok = user32.SystemParametersInfoW(
        SPI_SETSCREENREADER, 1 if enabled else 0, None, SPIF_SENDCHANGE
    )
    if not ok:
        raise OSError("SystemParametersInfoW(SPI_SETSCREENREADER) failed")


def restore() -> None:
    """Put the flag back exactly as we found it. Registered every which way."""
    global _original_state
    if _original_state is None:
        return
    try:
        current = get_screen_reader_flag()
        if current != _original_state:
            set_screen_reader_flag(_original_state)
            print(f"\n[restored] SPI_SCREENREADER -> {_original_state}")
    except Exception as exc:
        print(f"\n[WARNING] could not restore the flag: {exc}")
        print("          Run: python spikes/wake_probe.py --restore-off")
    finally:
        _original_state = None


def collect(match: list[str] | None, skipped: list[str] | None = None) -> dict[str, Probe]:
    """Probe every on-screen window, keyed by process name.

    Minimized windows report a zero-size rect and cannot be walked. They are
    recorded in `skipped` rather than dropped: an app missing from BEFORE and
    AFTER alike is not evidence of anything, and reading it as such is exactly
    how this experiment first ran without a control condition.
    """
    results: dict[str, Probe] = {}
    for window in auto.GetRootControl().GetChildren():
        try:
            if not window.Exists(0, 0):
                continue
            rect = window.BoundingRectangle
            if not rect or rect.width() <= 0:
                if skipped is not None:
                    skipped.append(_describe_skipped(window))
                continue
        except Exception:
            continue

        probe = probe_window(window)
        if probe.total < 5:
            continue
        if match and not any(m in probe.app.lower() for m in match):
            continue

        # Keep the richest window per process - apps often have several.
        existing = results.get(probe.app)
        if existing is None or probe.interactable > existing.interactable:
            results[probe.app] = probe
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match", help="comma-separated process substrings")
    parser.add_argument("--check", action="store_true",
                        help="just report the current flag state and exit")
    parser.add_argument("--restore-off", action="store_true",
                        help="force the flag off (recovery)")
    args = parser.parse_args()

    if args.check:
        print(f"SPI_SCREENREADER is currently: {get_screen_reader_flag()}")
        return

    if args.restore_off:
        set_screen_reader_flag(False)
        print("SPI_SCREENREADER forced to False.")
        return

    match = [m.strip().lower() for m in args.match.split(",")] if args.match else None

    global _original_state
    _original_state = get_screen_reader_flag()
    print(f"\nSPI_SCREENREADER was: {_original_state}")

    if _original_state:
        print(
            "\nThe flag is ALREADY set - a screen reader or assistive tool is\n"
            "running, which may be what woke Chrome. Close it and re-run\n"
            "uia_probe.py first, or this experiment has no control condition."
        )
        return

    # Make absolutely sure we put it back.
    atexit.register(restore)
    signal.signal(signal.SIGINT, lambda *_: (restore(), sys.exit(130)))

    try:
        print("\n--- BEFORE ---")
        skipped: list[str] = []
        before = collect(match, skipped)
        for name, probe in sorted(before.items()):
            print(f"  {name:<22} {probe.verdict:<9} {probe.interactable:>5} actionable")

        if skipped:
            print("\n  NOT WALKED - minimized, so absent from this experiment:")
            for entry in skipped:
                print(f"    {entry}")
            print("  Restore these and re-run, or they prove nothing either way.")

        print(f"\nSetting SPI_SETSCREENREADER, waiting {SETTLE_SECONDS}s to settle...")
        set_screen_reader_flag(True)
        time.sleep(SETTLE_SECONDS)

        print("\n--- AFTER ---")
        after = collect(match)
        for name, probe in sorted(after.items()):
            print(f"  {name:<22} {probe.verdict:<9} {probe.interactable:>5} actionable")

        _report_delta(before, after)

    finally:
        restore()


def _report_delta(before: dict[str, Probe], after: dict[str, Probe]) -> None:
    print("\n--- DELTA ---")
    header = f"{'PROCESS':<22} {'BEFORE':<20} {'AFTER':<20} {'VERDICT'}"
    print(header)
    print("-" * len(header))

    woke: list[str] = []
    for name in sorted(set(before) | set(after)):
        b = before.get(name)
        a = after.get(name)
        b_txt = f"{b.verdict} ({b.interactable})" if b else "-"
        a_txt = f"{a.verdict} ({a.interactable})" if a else "-"

        note = ""
        if b and a:
            if a.interactable > b.interactable * 2 and a.interactable > 20:
                note = "*** WOKE ***"
                woke.append(name)
            elif a.interactable > b.interactable:
                note = "improved"
        print(f"{name:<22} {b_txt:<20} {a_txt:<20} {note}")

    print()
    if woke:
        print(
            "CONCLUSION: SPI_SETSCREENREADER wakes Chromium/Electron trees.\n"
            f"            Woke: {', '.join(woke)}\n\n"
            "  This is the technique. Meow should set the flag while it runs\n"
            "  and clear it on exit - which is exactly what NVDA and JAWS do,\n"
            "  and legitimate here because Meow IS assistive technology.\n\n"
            "  Follow-ups worth measuring:\n"
            "    - memory/CPU cost while the flag is set (Chromium builds\n"
            "      full trees for every tab - this is not free)\n"
            "    - how long the wake takes (affects cold-start latency)\n"
            "    - whether apps launched AFTER the flag is set wake sooner\n"
            "    - side effects on other apps (animations, rendering)"
        )
    else:
        print(
            "CONCLUSION: the flag alone did not wake anything.\n\n"
            "  Next, in increasing cost:\n"
            "    1. VS Code only: set editor.accessibilitySupport to \"on\"\n"
            "       in settings.json and re-run uia_probe.py. VS Code has its\n"
            "       own switch independent of the system flag.\n"
            "    2. Relaunch VS Code with --force-renderer-accessibility\n"
            "       and re-run. If that works, the wake needs a relaunch,\n"
            "       which is intrusive but viable at app-launch time.\n"
            "    3. Open chrome://accessibility to see which mode flags\n"
            "       Chrome actually has on - that tells you what woke it.\n"
            "    4. If nothing wakes Electron: vision is the primary path\n"
            "       for those apps, and regime detection is the contribution."
        )


if __name__ == "__main__":
    main()
