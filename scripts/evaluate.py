"""Run the ablation - Phase 1.10.

Asks both grounding strategies to find the same controls in whatever window is
in front, and scores them against the rectangles UIA reports.

    python scripts/evaluate.py --tasks 8
    python scripts/evaluate.py --tasks 8 --no-vision   # free, UIA only

Vision costs one model call per task, with a screenshot. At roughly 2,833
tokens each that is about a third of a cent for eight tasks - small, but not
nothing on a five dollar budget, so it is opt-out and the count is printed.

Put the window you want to measure in front and give it a couple of seconds.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meow.console import use_utf8_console
from meow.evaluation import evaluate
from meow.grounding import UIAGrounding, VisionGrounding
from meow.mind import Mind
from meow.platform.capture import capture_screens
from meow.platform.dpi import enable_per_monitor_dpi_awareness
from meow.uia import digest_foreground


def main() -> None:
    use_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=8)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--vision-detail", choices=["low", "high"],
                        default="low",
                        help="high is 13x the tokens and answers whether the "
                             "baseline was simply starved of pixels")
    parser.add_argument("--no-vision", action="store_true",
                        help="skip the vision baseline, which costs tokens")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="seconds to wait so you can focus a window")
    args = parser.parse_args()

    enable_per_monitor_dpi_awareness()

    if args.delay:
        print(f"\n  focus the window you want to measure - starting in "
              f"{args.delay:.0f}s")
        time.sleep(args.delay)

    # ONE screen, captured once, for every strategy and every task. Without
    # this the experiment partly measures how much the window moved during it:
    # a vision call takes about 2.7 seconds, and over several tasks that is a
    # live application scrolling out from under the ground truth. Measured, it
    # cost UIA half its score - 3/6 instead of 6/6, for controls that had
    # simply moved since they were recorded.
    frozen_digest = digest_foreground()
    frozen_shots = capture_screens()

    strategies = {"uia": UIAGrounding(frozen=frozen_digest)}
    mind = None
    if not args.no_vision:
        mind = Mind()
        mind.screen.force_high_detail = args.vision_detail == "high"
        strategies["vision"] = VisionGrounding(mind, capture_screens,
                                               frozen=frozen_shots)
        print(f"  vision baseline on, about {args.tasks} model calls\n")

    report = evaluate(strategies, tasks=args.tasks, seed=args.seed,
                      digest=frozen_digest)
    print(report.summary())

    if mind is not None:
        print(f"\n  spend: {mind.screen.budget.summary()}")

    print("\n  per task:")
    for attempt in report.attempts:
        mark = "ok " if attempt.correct else "MISS"
        miss = f"{attempt.distance:.0f}px" if attempt.distance is not None else "-"
        print(f"    {mark} {attempt.strategy:<7} {attempt.task[:26]:<28} "
              f"{miss:>8}  {attempt.failure.value}")


if __name__ == "__main__":
    main()
