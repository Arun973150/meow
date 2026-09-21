"""The ablation - Phase 1.10.

Does asking Windows where a control is beat asking a model to guess from pixels?
This measures it.

**Ground truth comes free, and that is the design.** Every other way of running
this experiment needs someone to sit and label screenshots: this button is here,
that one is there. But UIA already returns the exact rectangle of every control,
from the operating system that drew it. So the tree labels the data for the
vision system to be tested against, automatically, on whatever happens to be on
screen - which means the suite is not a fixed set of screenshots that slowly
stops resembling anything real.

A vision answer counts as correct when the point it returns falls **inside the
rectangle UIA reports**. That is exactly the criterion that matters in practice,
because a click lands where the point is.

The obvious objection: this assumes UIA is right. For controls UIA exposes, that
is a safe assumption - it is the operating system's own answer about its own
widgets, and it is what the screen reader uses. Where it is NOT safe is the
EMPTY regime, canvas and custom-drawn surfaces, where there is no tree to be
right. Those tasks need hand-labelling and are excluded here rather than
silently scored against nothing.

Reporting one aggregate number hides the result. Per regime, per failure mode.
"""

from __future__ import annotations

import random
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from .grounding import Grounding, Source, Target
from .uia import Element, Regime, WindowDigest, digest_foreground


class Failure(Enum):
    """Why a task failed. Counted separately because they mean different things."""

    NONE = "ok"
    # Located something, but not the thing asked for. The dangerous one: a
    # click lands on the wrong control and nobody notices.
    WRONG_ELEMENT = "wrong element"
    # Found nothing. Visible and recoverable - the user can rephrase.
    NOT_FOUND = "not found"
    # The element existed but the digest did not include it. Ours to fix, not
    # the platform's, and the reason it is counted apart from NOT_FOUND.
    DROPPED_BY_DIGEST = "dropped by digest"
    TIMEOUT = "timeout"


@dataclass
class Attempt:
    task: str
    strategy: str
    correct: bool
    failure: Failure
    milliseconds: float
    truth: tuple[int, int] | None = None
    guess: tuple[int, int] | None = None
    distance: float | None = None


@dataclass
class Report:
    attempts: list[Attempt] = field(default_factory=list)
    regime: Regime | None = None
    app: str = ""
    digest_size: int = 0
    total_controls: int = 0

    def by_strategy(self) -> dict[str, list[Attempt]]:
        grouped: dict[str, list[Attempt]] = {}
        for attempt in self.attempts:
            grouped.setdefault(attempt.strategy, []).append(attempt)
        return grouped

    def summary(self) -> str:
        lines = [
            f"{self.app}  regime={self.regime.value if self.regime else '?'}  "
            f"{self.digest_size} of {self.total_controls} controls in the digest",
            "",
            f"  {'strategy':<10} {'hit rate':>9} {'median ms':>10} "
            f"{'median miss px':>15}",
            "  " + "-" * 48,
        ]
        for strategy, attempts in sorted(self.by_strategy().items()):
            hits = sum(1 for a in attempts if a.correct)
            times = sorted(a.milliseconds for a in attempts)
            distances = sorted(a.distance for a in attempts
                               if a.distance is not None)
            median_time = times[len(times) // 2] if times else 0.0
            median_distance = (f"{distances[len(distances)//2]:.0f}"
                               if distances else "-")
            lines.append(
                f"  {strategy:<10} {hits}/{len(attempts):<3} "
                f"{hits/max(1,len(attempts))*100:>4.0f}% "
                f"{median_time:>9.0f} {median_distance:>15}"
            )

        lines.append("")
        lines.append("  failures by kind:")
        for strategy, attempts in sorted(self.by_strategy().items()):
            kinds = Counter(a.failure.value for a in attempts
                            if a.failure is not Failure.NONE)
            detail = ", ".join(f"{count} {kind}"
                               for kind, count in kinds.most_common()) or "none"
            lines.append(f"    {strategy:<10} {detail}")
        return "\n".join(lines)


def _inside(element: Element, point: tuple[int, int]) -> bool:
    """Would a click at this point land on this control?

    The criterion is deliberately the practical one rather than a distance
    threshold. Twenty pixels out is a hit on a wide toolbar button and a miss on
    a close box, and the difference is what actually matters.
    """
    x, y = point
    return element.left <= x <= element.right and element.top <= y <= element.bottom


def sample_tasks(digest: WindowDigest, count: int,
                 seed: int | None = None) -> list[Element]:
    """Pick controls to ask about.

    Prefers short, distinct names. A control called "uia.py (9iloms), preview,
    Editor Group 1" is not something a person would ever say out loud, so
    scoring a system on finding it measures nothing useful.
    """
    usable = [
        element for element in digest.elements
        if 2 <= len(element.name) <= 28 and element.enabled
    ]
    # Ambiguous names make the ground truth ambiguous too: with three controls
    # called "Close", a hit on any of them is arguably correct and the score
    # stops meaning anything.
    names = Counter(element.name.lower() for element in usable)
    distinct = [element for element in usable if names[element.name.lower()] == 1]

    random.Random(seed).shuffle(distinct)
    return distinct[:count]


def evaluate(strategies: dict[str, Grounding], tasks: int = 12,
             seed: int | None = 7,
             digest: WindowDigest | None = None) -> Report:
    """Run every strategy against the same controls in the same window.

    Pass `digest` to pin the ground truth to a screen captured once. Letting
    each call re-read is how a live window scrolls out from under the
    experiment - it cost UIA half its score in the first run here.
    """
    digest = digest if digest is not None else digest_foreground()
    if digest is None:
        raise RuntimeError("No foreground window to evaluate against.")

    if digest.regime is Regime.EMPTY:
        raise RuntimeError(
            "This window exposes no accessibility tree, so there is no ground "
            "truth to score against. EMPTY-regime tasks need hand-labelling."
        )

    chosen = sample_tasks(digest, tasks, seed)
    report = Report(
        regime=digest.regime,
        app=digest.app,
        digest_size=len(digest.elements),
        total_controls=digest.total_found,
    )

    for element in chosen:
        truth = element.centre
        for name, strategy in strategies.items():
            started = time.perf_counter()
            try:
                target: Target | None = strategy.locate(element.name)
            except Exception:  # noqa: BLE001 - a crash is a failed attempt
                target = None
            milliseconds = (time.perf_counter() - started) * 1000

            if target is None:
                report.attempts.append(Attempt(
                    task=element.name, strategy=name, correct=False,
                    failure=Failure.NOT_FOUND, milliseconds=milliseconds,
                    truth=truth,
                ))
                continue

            guess = target.centre
            correct = _inside(element, guess)
            distance = ((guess[0] - truth[0]) ** 2
                        + (guess[1] - truth[1]) ** 2) ** 0.5

            failure = Failure.NONE if correct else Failure.WRONG_ELEMENT
            if not correct and target.source is Source.UIA:
                # UIA found *a* control and it was the wrong one, which means
                # the name matched something else - a selection problem rather
                # than a grounding one.
                failure = Failure.WRONG_ELEMENT

            report.attempts.append(Attempt(
                task=element.name, strategy=name, correct=correct,
                failure=failure, milliseconds=milliseconds,
                truth=truth, guess=guess, distance=distance,
            ))

    return report
