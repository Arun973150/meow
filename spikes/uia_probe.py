"""
UIA Probe - Day One Spike
=========================

Answers the question the whole project's thesis rests on:

    How much of a real application is actually visible through the
    Windows UI Automation tree?

MEASURED on this machine, at full depth:

    RICH      native Win32/WPF/WinForms (Explorer, Settings, Notepad)
              -> ~75 named, actionable elements with bounding rects

    DENSE     Chromium/Electron (Chrome, VS Code)
              -> 150-820 actionable elements. NOT skeletal. Content is
                 nested ~30 levels below the frame, which is what an
                 earlier version of this script, capped at depth 12,
                 mistook for an empty tree.

    EMPTY     games, canvas, custom-drawn surfaces (still expected;
              not yet observed here)

The original hypothesis was that Chromium apps expose nothing without
--force-renderer-accessibility. That was wrong. Both the system-wide
SPI_SETSCREENREADER flag and VS Code's editor.accessibilitySupport were
A/B tested and neither changed anything; see spikes/wake_probe.py and
docs/02-grounding.md.

The problem is not that the tree is missing. It is that VS Code's tree has
819 actionable elements and the model's digest budget is ~150.

Usage
-----
    pip install uiautomation
    python spikes/uia_probe.py                 # probe whatever is open
    python spikes/uia_probe.py --json out.json # also dump raw results

Open the apps you care about before running. Nothing is clicked, typed,
or modified - this only reads the tree.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import dataclass, field, asdict

try:
    import uiautomation as auto
except ImportError:
    raise SystemExit(
        "uiautomation is not installed.\n\n"
        "    pip install uiautomation\n"
    )


# Control types that an agent could plausibly act on. If these are absent,
# the tree is decoration - we can see the window but not operate it.
INTERACTABLE = {
    "ButtonControl", "EditControl", "MenuItemControl", "CheckBoxControl",
    "ComboBoxControl", "ListItemControl", "TabItemControl", "HyperlinkControl",
    "RadioButtonControl", "SliderControl", "TreeItemControl", "SplitButtonControl",
    "DocumentControl", "TextControl", "DataItemControl", "ToolBarControl",
}

# A Chromium/Electron shell with no content exposed looks like a handful of
# these and nothing else.
SHELL_ONLY = {"PaneControl", "TitleBarControl", "WindowControl", "GroupControl"}

# Walking the full tree can take minutes on a deep app. Cap both, and treat
# a timeout as a finding in its own right - a tree too slow to walk is a
# tree we cannot use inside a sub-second voice loop.
# Electron nests window content roughly 30 levels below the frame. A cap of 12
# reported VS Code as SKELETAL with 6 actionable elements when the tree in fact
# holds 782 - and sent us hunting a Chromium "wake mechanism" that did not
# exist. Keep real headroom here, and trust `truncated` over any verdict.
MAX_DEPTH = 50
TIME_BUDGET_SEC = 8.0


@dataclass
class Probe:
    app: str
    window_title: str
    total: int = 0
    named: int = 0
    interactable: int = 0
    with_rect: int = 0
    max_depth: int = 0
    walk_seconds: float = 0.0
    truncated: bool = False
    control_types: dict[str, int] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        """Classify the tree into one of the three expected regimes.

        A walk that hit a cap has not seen the tree, so it gets no verdict at
        all. Calling a truncated walk SKELETAL is what made VS Code look like
        a Chromium accessibility problem instead of a bug in this walker.
        """
        if self.truncated:
            return "TRUNCATED"
        if self.total <= 3 or self.interactable == 0:
            return "EMPTY"
        # The Chromium signature: a shell of panes with almost nothing
        # actionable inside it, even though the window is clearly full of
        # buttons a human can see.
        non_shell = sum(
            count for ctype, count in self.control_types.items()
            if ctype not in SHELL_ONLY
        )
        if self.interactable < 10 or non_shell < 10:
            return "SKELETAL"
        return "RICH"

    def summary_line(self) -> str:
        return (
            f"{self.app[:22]:<22} {self.verdict:<9} "
            f"{self.total:>6} {self.named:>7} {self.interactable:>7} "
            f"{self.with_rect:>7} {self.max_depth:>6} "
            f"{self.walk_seconds:>7.2f}s"
            + ("  (truncated)" if self.truncated else "")
        )


def walk(element, probe: Probe, depth: int = 0, started: float = 0.0,
         max_depth: int = MAX_DEPTH) -> None:
    """Depth-first walk, counting what an agent could actually use."""
    if depth > max_depth:
        probe.truncated = True
        return
    if time.perf_counter() - started > TIME_BUDGET_SEC:
        probe.truncated = True
        return

    probe.total += 1
    probe.max_depth = max(probe.max_depth, depth)

    ctype = element.ControlTypeName
    probe.control_types[ctype] = probe.control_types.get(ctype, 0) + 1

    try:
        if element.Name and element.Name.strip():
            probe.named += 1
    except Exception:
        pass

    if ctype in INTERACTABLE:
        probe.interactable += 1

    try:
        rect = element.BoundingRectangle
        if rect and rect.width() > 0 and rect.height() > 0:
            probe.with_rect += 1
    except Exception:
        pass

    try:
        children = element.GetChildren()
    except Exception:
        return

    for child in children:
        walk(child, probe, depth + 1, started, max_depth)


def probe_window(window, max_depth: int = MAX_DEPTH) -> Probe:
    try:
        title = window.Name or "(untitled)"
    except Exception:
        title = "(unreadable)"

    try:
        app_name = window.ProcessId and _process_name(window.ProcessId) or "?"
    except Exception:
        app_name = "?"

    probe = Probe(app=app_name, window_title=title[:60])

    started = time.perf_counter()
    walk(window, probe, 0, started, max_depth)
    probe.walk_seconds = time.perf_counter() - started
    return probe


def _process_name(pid: int) -> str:
    """Best-effort process name without adding a psutil dependency."""
    import subprocess
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if out and "," in out:
            return out.split(",")[0].strip('"')
    except Exception:
        pass
    return f"pid:{pid}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", help="write raw results to this path")
    parser.add_argument(
        "--max-depth", type=int, default=MAX_DEPTH,
        help=(
            "how deep to walk. Chromium nests content inside long chains of "
            "panes, so a shallow cap can report SKELETAL for a tree that is "
            "actually populated further down. Raise this before concluding "
            "an app exposes nothing."
        ),
    )
    parser.add_argument(
        "--min-elements", type=int, default=5,
        help="skip windows smaller than this (tooltips, tray popups)",
    )
    args = parser.parse_args()

    print("\nWalking top-level windows. Nothing is clicked or modified.\n")

    desktop = auto.GetRootControl()
    windows = desktop.GetChildren()

    header = (
        f"{'PROCESS':<22} {'VERDICT':<9} {'TOTAL':>6} {'NAMED':>7} "
        f"{'ACTION':>7} {'RECTS':>7} {'DEPTH':>6} {'WALK':>8}"
    )
    print(header)
    print("-" * len(header))

    results: list[Probe] = []
    minimized: list[str] = []
    for window in windows:
        try:
            if not window.Exists(0, 0):
                continue
            rect = window.BoundingRectangle
            if not rect or rect.width() <= 0:
                # A MINIMIZED window reports a zero or off-screen rect and is
                # invisible to this walk. That is not the same as an occluded
                # window, which UIA reads fine. Report it rather than dropping
                # it silently - a missing app here once cost us the control
                # condition in the wake experiment.
                minimized.append(_describe_skipped(window))
                continue
        except Exception:
            continue

        probe = probe_window(window, args.max_depth)
        if probe.total < args.min_elements:
            continue

        results.append(probe)
        print(probe.summary_line())

    print()
    if minimized:
        print("NOT WALKED - minimized, zero-size rect (restore and re-run to include):")
        for entry in minimized:
            print(f"  {entry}")
        print()
    _report(results)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump([asdict(p) for p in results], handle, indent=2)
        print(f"\nRaw results written to {args.json}")



def _describe_skipped(window) -> str:
    """Name a window we could not walk, so its absence is visible in output."""
    try:
        title = (window.Name or "(untitled)")[:48]
    except Exception:
        title = "(unreadable)"
    try:
        app_name = _process_name(window.ProcessId)
    except Exception:
        app_name = "?"
    return f"{app_name:<22} {title}"

def _report(results: list[Probe]) -> None:
    """Interpret the numbers so the spike answers its own question."""
    if not results:
        print("No windows probed. Open some apps and run again.")
        return

    tally = Counter(p.verdict for p in results)
    print(
        f"REGIMES   rich={tally['RICH']}  "
        f"skeletal={tally['SKELETAL']}  empty={tally['EMPTY']}  "
        f"truncated={tally['TRUNCATED']}"
    )
    if tally["TRUNCATED"]:
        print(
            "  => A truncated walk proves nothing. Re-run with a higher\n"
            "     --max-depth before reading anything into these rows."
        )

    chromium_markers = ("chrome", "code", "slack", "discord", "teams", "cursor", "msedge")
    chromium = [
        p for p in results
        if any(marker in p.app.lower() for marker in chromium_markers)
    ]

    if chromium:
        print("\nCHROMIUM / ELECTRON APPS")
        for probe in chromium:
            print(f"  {probe.app:<22} {probe.verdict:<9} "
                  f"{probe.interactable} actionable elements")

        if all(p.verdict != "RICH" for p in chromium):
            print(
                "\n  => No Chromium app came back RICH.\n"
                "     Before concluding anything, re-run with --max-depth 50.\n"
                "     Electron content sits ~30 levels below the frame, and a\n"
                "     shallow cap reports SKELETAL for a fully populated tree.\n"
                "     Only if it is still skeletal at depth is vision the\n"
                "     primary path for these apps."
            )
        else:
            print("\n  => At least one Chromium app exposed a usable tree.\n"
                  "     Find out what woke it - that is the technique to copy.")
    else:
        print(
            "\nNo Chromium/Electron apps were open. Open Chrome, VS Code or\n"
            "Slack and run again - that is the case the thesis depends on."
        )

    slow = [p for p in results if p.walk_seconds > 1.0]
    if slow:
        print("\nSLOW TREES (a full walk will not fit a sub-second voice loop)")
        for probe in slow:
            print(f"  {probe.app:<22} {probe.walk_seconds:.2f}s "
                  f"for {probe.total} elements")
        print("  => The digest must be filtered and capped, not walked whole.")


if __name__ == "__main__":
    main()
