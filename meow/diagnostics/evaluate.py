"""The ablation across applications, not just whichever window is in front.

    python scripts/evaluate_suite.py                    # every app it can reach
    python scripts/evaluate_suite.py --no-vision        # free, UIA only
    python scripts/evaluate_suite.py --apps chrome,word
    python scripts/evaluate_suite.py --per-app 5

`scripts/evaluate.py` measures one window, and the first published result came
from six tasks in VS Code. That is a pilot: it shows the experiment runs, not
that the claim holds. docs/04-evaluation.md asks for about thirty tasks across
six applications spanning regimes, because a grounding strategy that wins on
one dense Electron window has been shown to win on one dense Electron window.

This drives the same experiment across applications and pools the results.

**What it does not do is hide a failure.** An application that will not come
forward, a window that is minimised and therefore reports a zero-size rect, a
walk that hits the depth cap - each is reported by name with the reason, and
none of them is quietly skipped. An absent app silently dropped reads as a
negative result for whichever strategy is being measured, and that is how a
tool like this produces a confident wrong number.

**Each application is frozen before it is measured.** One digest and one set of
screenshots per app, shared by every strategy and every task, because a vision
call takes about 2.7 seconds and a live window scrolls out from under the
ground truth while the experiment runs. Measured on VS Code, not freezing cost
UIA half its score - 3/6 rather than 6/6 - for controls that had merely moved.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from collections import Counter
from pathlib import Path


from meow.desktop import apps
from meow.console import quiet_library_warnings, use_utf8_console
from meow.agent.evaluation import Failure, Report, evaluate
from meow.desktop.grounding import (
    StrictVisionGrounding, UIAGrounding, VisionGrounding,
)
from meow.agent.mind import Mind
from meow.platform.capture import capture_screens
from meow.platform.dpi import enable_per_monitor_dpi_awareness
from meow.desktop.uia import Regime, digest_foreground

# The suite from docs/04-evaluation.md. Ordered so the cheap, reliable ones run
# first: if something is going to go wrong with focus or launching, it is worth
# finding out before spending the vision budget.
SUITE = [
    ("settings", "Settings"),
    ("file explorer", "Explorer"),
    ("chrome", "Chrome"),
    ("notepad", "Notepad"),
    ("word", "Word"),
    ("code", "VS Code"),
]

# How long an application gets to come forward and settle. Focus does not
# arrive on the first try - a freshly launched Notepad has been seen to leave
# focus on a button, on a group, and once on an unrelated window - so this is
# retried rather than waited out in one go.
FOCUS_ATTEMPTS = 8
FOCUS_WAIT_SECONDS = 0.7

# Applications this launched, to be closed again afterwards. Leaving six
# windows open because a measurement script opened them is rude.
LAUNCH_SETTLE_SECONDS = 3.0


def matching_windows(name: str) -> list:
    """Every open window that could be this application, best guess first."""
    wanted = name.lower().split()
    scored = []
    for window in apps.list_windows():
        haystack = f"{window.process} {window.title}".lower()
        hits = sum(1 for word in wanted if word in haystack)
        if hits:
            # ApplicationFrameHost owns the real top-level window for UWP
            # applications, so it is tried first when both are present.
            frame_host = "applicationframehost" in window.process.lower()
            scored.append((hits, frame_host, window))
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in scored]


def looks_like(name: str, digest) -> bool:
    haystack = f"{digest.app} {digest.title}".lower()
    return any(word in haystack for word in name.lower().split())


def settle_foreground() -> None:
    """Give Windows a moment to put SOMETHING in front.

    A refused focus change can leave GetForegroundWindow returning zero. The
    next application then cannot be focused either, because Windows only lets
    the foreground process hand focus on - so one failure cascades into every
    measurement after it unless it is waited out.
    """
    for _ in range(6):
        if ctypes.windll.user32.GetForegroundWindow():
            return
        time.sleep(0.4)


def bring_forward(name: str) -> tuple[object | None, str]:
    """Focus an application, launching it if needed. Returns (window, note)."""
    candidates = matching_windows(name)
    launched = False

    if not candidates:
        application = apps.find_application(name)
        if application is None:
            return None, "not installed"
        if not apps.launch(application):
            return None, "would not launch"
        launched = True
        time.sleep(LAUNCH_SETTLE_SECONDS)
        candidates = matching_windows(name)
        if not candidates:
            return None, "launched but no window appeared"

    # EVERY matching window, not just the first. A UWP application appears
    # twice - Settings is listed under both SystemSettings.exe and
    # ApplicationFrameHost.exe - and only the frame host's window can be
    # brought forward. Focusing the other one fails, and it fails by leaving
    # the desktop with NO foreground window at all, which then breaks the
    # focus of whatever is measured next.
    for window in candidates:
        for _ in range(FOCUS_ATTEMPTS):
            apps.focus_window(window)
            time.sleep(FOCUS_WAIT_SECONDS)
            digest = digest_foreground()
            # Elements required, not just the right name. A window still
            # mid-restore answers with its own title and a zero-size rect,
            # so accepting it here measured an empty window and then left
            # it in front, which blocked the application after it too.
            if (digest is not None and digest.elements
                    and looks_like(name, digest)):
                note = "launched" if launched else "already open"
                if len(candidates) > 1:
                    note += f" ({window.process})"
                return window, note

    settle_foreground()
    front = digest_foreground()
    got = front.app if front is not None else "nothing"
    tried = ", ".join(sorted({w.process for w in candidates}))
    return None, f"would not come forward (tried {tried}; front was {got})"


def measure(label: str, tasks: int, seed: int, mind: Mind | None,
            vision_detail: str, strict: bool = False
            ) -> tuple[Report | None, str]:
    """Freeze this application and run every strategy against it."""
    frozen_digest = digest_foreground()
    if frozen_digest is None:
        return None, "no digest - window unreadable"
    if not frozen_digest.elements:
        # A minimised window reports a zero-size rect and walks to nothing.
        # Reported, never dropped: an empty result looks identical to a
        # strategy scoring zero, and they mean opposite things.
        return None, "zero controls - minimised, or nothing actionable"
    if frozen_digest.truncated:
        return None, "walk was TRUNCATED - depth or element cap hit"

    frozen_shots = capture_screens()
    strategies = {"uia": UIAGrounding(frozen=frozen_digest)}
    if mind is not None:
        mind.screen.force_high_detail = vision_detail == "high"
        mind.screen.forget()
        strategies["vision"] = VisionGrounding(mind, capture_screens,
                                               frozen=frozen_shots)
        if strict:
            # The control condition. Answers the objection that the baseline
            # was never really measured because it kept declining to answer.
            strategies["vision-strict"] = StrictVisionGrounding(
                mind, capture_screens, frozen=frozen_shots)

    report = evaluate(strategies, tasks=tasks, seed=seed, digest=frozen_digest)
    return report, "ok"


def pooled(reports: dict[str, Report]) -> str:
    """Every attempt from every application, counted once."""
    everything = [attempt for report in reports.values()
                  for attempt in report.attempts]
    by_strategy: dict[str, list] = {}
    for attempt in everything:
        by_strategy.setdefault(attempt.strategy, []).append(attempt)

    lines = [
        "",
        f"  POOLED  {len(everything)} attempts across {len(reports)} applications",
        "",
        f"  {'strategy':<10} {'hit rate':>12} {'median miss':>13} {'median ms':>11}",
        "  " + "-" * 50,
    ]
    for strategy, attempts in sorted(by_strategy.items()):
        hits = sum(1 for a in attempts if a.correct)
        distances = sorted(a.distance for a in attempts if a.distance is not None)
        times = sorted(a.milliseconds for a in attempts)
        median_distance = (f"{distances[len(distances) // 2]:.0f} px"
                           if distances else "-")
        median_time = times[len(times) // 2] if times else 0.0
        lines.append(f"  {strategy:<10} {hits:>3}/{len(attempts):<3} "
                     f"{hits / max(1, len(attempts)) * 100:>4.0f}% "
                     f"{median_distance:>13} {median_time:>10.0f}")

    lines += ["", "  failure modes, pooled:"]
    for strategy, attempts in sorted(by_strategy.items()):
        kinds = Counter(a.failure.value for a in attempts
                        if a.failure is not Failure.NONE)
        detail = ", ".join(f"{count} {kind}" for kind, count
                           in kinds.most_common()) or "none"
        lines.append(f"    {strategy:<10} {detail}")
    return "\n".join(lines)


def by_regime(reports: dict[str, Report]) -> str:
    """Does the result hold in sparse windows as well as dense ones?

    This is the question a single-application run cannot answer, and the one
    most likely to embarrass the claim: a strategy that reads the tree should
    do BETTER where the tree is thin, and if it does not, the digest filter is
    the thing being measured rather than the platform.
    """
    grouped: dict[str, dict[str, list]] = {}
    for report in reports.values():
        regime = report.regime.value if report.regime else "unknown"
        for attempt in report.attempts:
            grouped.setdefault(regime, {}).setdefault(
                attempt.strategy, []).append(attempt)

    lines = ["", "  by regime:", ""]
    for regime, strategies in sorted(grouped.items()):
        parts = []
        for strategy, attempts in sorted(strategies.items()):
            hits = sum(1 for a in attempts if a.correct)
            parts.append(f"{strategy} {hits}/{len(attempts)}")
        lines.append(f"    {regime:<12} {'   '.join(parts)}")
    return "\n".join(lines)


def main() -> None:
    use_utf8_console()
    quiet_library_warnings()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-app", type=int, default=5,
                        help="tasks per application")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--apps", default="",
                        help="comma-separated subset, default is the whole suite")
    parser.add_argument("--vision-detail", choices=["low", "high"], default="low")
    parser.add_argument("--strict", action="store_true",
                        help="also run the coordinates-only control, which "
                             "answers whether the baseline was simply "
                             "refusing to answer")
    parser.add_argument("--no-vision", action="store_true",
                        help="skip the vision baseline, which costs tokens")
    args = parser.parse_args()

    enable_per_monitor_dpi_awareness()

    wanted = ([(name.strip(), name.strip().title())
               for name in args.apps.split(",") if name.strip()]
              if args.apps else SUITE)

    mind = None
    if not args.no_vision:
        mind = Mind()
        calls = len(wanted) * args.per_app
        print(f"\n  vision baseline on - about {calls} model calls")

    print(f"  {args.per_app} tasks per application, seed {args.seed}\n")

    reports: dict[str, Report] = {}
    skipped: list[tuple[str, str]] = []

    for name, label in wanted:
        print(f"  --- {label} " + "-" * (56 - len(label)))
        window, note = bring_forward(name)
        if window is None:
            print(f"      SKIPPED: {note}\n")
            skipped.append((label, note))
            continue

        report, why = measure(label, args.per_app, args.seed, mind,
                              args.vision_detail, strict=args.strict)
        if report is None:
            print(f"      SKIPPED: {why}\n")
            skipped.append((label, why))
            continue

        reports[label] = report
        print("      " + report.summary().replace("\n", "\n      "))
        print()

    if not reports:
        raise SystemExit("\n  nothing could be measured - see the skips above\n")

    print("=" * 66)
    print(pooled(reports))
    print(by_regime(reports))

    if skipped:
        # Printed at the end as well as inline, because a skip that scrolls
        # past is a skip nobody accounts for when reading the pooled number.
        print("\n  NOT MEASURED, and why:")
        for label, why in skipped:
            print(f"    {label:<14} {why}")
        print(f"\n  {len(reports)} of {len(wanted)} applications measured. "
              f"The pooled number covers those, not the suite.")

    if mind is not None:
        print(f"\n  spend: {mind.screen.budget.summary()}")


if __name__ == "__main__":
    main()
