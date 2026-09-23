"""Scoring the strategies against targets neither of them chose.

    meow evaluate --labelled

The difference from the sampled ablation is the whole point. There, the task
was a control's own name taken out of the digest and the truth was that same
control's rectangle, so `locate("Close")` against a digest containing "Close"
could only ever succeed. Here the words are a person's and the point is where
that person said it was, and both were recorded before either strategy ran.

Three conditions, because two of them are different claims:

    uia            `digest.find(description)` - fuzzy string matching against
                   control names, no model. This is the strategy as a library.
    uia+model      the model reads the digest and NAMES a control, which is
                   what Meow actually does on every act turn. The honest
                   representation of the product.
    vision         the model reads a screenshot and gives a coordinate. The
                   Clicky baseline, unchanged.

A hit is the returned rectangle CONTAINING the hand-marked point. Distance is
reported from the rectangle's centre so the two ablations stay comparable.

`in_digest` is reported separately and given to nobody. It answers whether the
target was in the tree at all, which separates UIA failing to find something
it could see from being asked for something it never had - two failures that
look identical in a hit rate and mean opposite things.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..desktop.grounding import Target
from ..desktop.uia import Element, Regime, WindowDigest

MODEL = "gpt-4o-mini"

NAME_THE_CONTROL = """You are looking at a list of controls on a Windows screen.

The user said: "{description}"

Reply with the EXACT name of the one control they mean, copied character for
character from the list, and nothing else. If none of them is what they mean,
reply exactly: NONE

Controls:
{controls}"""


@dataclass
class Outcome:
    description: str
    strategy: str
    hit: bool
    distance: float | None
    milliseconds: float
    answered: bool           # did it return anything at all
    app: str = ""
    in_digest: bool = False  # analysis only; never shown to a strategy


@dataclass
class HeldOutReport:
    outcomes: list[Outcome] = field(default_factory=list)
    labels: int = 0

    def by_strategy(self) -> dict[str, list[Outcome]]:
        grouped: dict[str, list[Outcome]] = {}
        for outcome in self.outcomes:
            grouped.setdefault(outcome.strategy, []).append(outcome)
        return grouped

    def summary(self) -> str:
        lines = [
            "",
            f"  {self.labels} hand-labelled targets, "
            f"described by a person, marked by a person.",
            "",
            f"  {'strategy':14} {'hits':>9}  {'median miss':>12}  "
            f"{'answered':>9}  {'median ms':>9}",
            "  " + "-" * 62,
        ]
        for name, outcomes in self.by_strategy().items():
            hits = sum(1 for o in outcomes if o.hit)
            misses = sorted(o.distance for o in outcomes
                            if o.distance is not None and not o.hit)
            median = f"{misses[len(misses) // 2]:,.0f} px" if misses else "-"
            answered = sum(1 for o in outcomes if o.answered)
            times = sorted(o.milliseconds for o in outcomes)
            lines.append(
                f"  {name:14} {hits:>4}/{len(outcomes):<4} {median:>12}  "
                f"{answered:>4}/{len(outcomes):<4} "
                f"{times[len(times) // 2]:>8,.0f}")

        # Per target, because the rate alone hides the shape of the failure.
        # "It gets pieces and misses coordinates" is actionable; "65%" is not.
        lines += ["", "  each target:"]
        # Keyed by POSITION as well as description. Two labels were both
        # called "square infront of king" and one silently overwrote the
        # other - seventeen labels, sixteen rows.
        ordered: dict = {}
        for outcome in self.outcomes:
            key = (outcome.description, outcome.app)
            slot = ordered.setdefault(key, [])
            existing = next((row for row in slot
                             if outcome.strategy not in row), None)
            if existing is None:
                slot.append({})
                existing = slot[-1]
            existing[outcome.strategy] = outcome
        by_description = {}
        for (description, _app), rows in ordered.items():
            for number, row in enumerate(rows, 1):
                suffix = f" #{number}" if len(rows) > 1 else ""
                by_description[description + suffix] = row
        for description, results in by_description.items():
            marks = []
            for name, outcome in results.items():
                if outcome.hit:
                    marks.append(f"{name} HIT")
                elif outcome.answered:
                    marks.append(f"{name} {outcome.distance:.0f}px")
                else:
                    marks.append(f"{name} -")
            lines.append(f"    {description[:30]:32} {' | '.join(marks)}")

        apps = sorted({o.app for o in self.outcomes if o.app})
        if len(apps) > 1:
            lines += ["", "  by application:"]
            for app in apps:
                parts = []
                for name in sorted({o.strategy for o in self.outcomes}):
                    here = [o for o in self.outcomes
                            if o.app == app and o.strategy == name]
                    if here:
                        parts.append(f"{name} "
                                     f"{sum(1 for o in here if o.hit)}/{len(here)}")
                lines.append(f"    {app:22} {'  '.join(parts)}")

        in_tree = sum(1 for o in self.outcomes
                      if o.in_digest and o.strategy == "uia")
        total = sum(1 for o in self.outcomes if o.strategy == "uia")
        if total:
            lines += [
                "",
                f"  {in_tree}/{total} of these targets were in the UIA digest "
                f"at all.",
                "  A target the tree never had is not a failure to find it.",
            ]
        return "\n".join(lines)


def _rebuild(path: Path) -> WindowDigest | None:
    """The digest exactly as it was when the label was made."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    elements = [
        Element(name=item["name"], role=item["role"], left=item["left"],
                top=item["top"], right=item["right"], bottom=item["bottom"],
                enabled=item.get("enabled", True))
        for item in raw.get("elements", [])
    ]
    return WindowDigest(
        app=raw.get("app", ""), title=raw.get("title", ""), elements=elements,
        regime=Regime[raw.get("regime", "RICH")],
        total_found=raw.get("total_found", len(elements)),
        usable_found=len(elements), query_seconds=0.0)


def _contains(target: Target, point: tuple[int, int]) -> bool:
    x, y = point
    return (target.left <= x <= target.right
            and target.top <= y <= target.bottom)


def _distance(target: Target, point: tuple[int, int]) -> float:
    centre = target.centre
    return ((centre[0] - point[0]) ** 2 + (centre[1] - point[1]) ** 2) ** 0.5


def _uia_plain(digest: WindowDigest, description: str) -> Target | None:
    element = digest.find(description)
    return Target.from_element(element) if element else None


def _uia_with_model(digest: WindowDigest, description: str, model) -> Target | None:
    """What the product does: the model reads the list and names one."""
    controls = "\n".join(f"- {element.name}" for element in digest.elements[:120])
    try:
        reply = model.invoke(
            NAME_THE_CONTROL.format(description=description, controls=controls))
    except Exception:  # noqa: BLE001 - a failed call is a failed attempt
        return None
    named = str(getattr(reply, "content", "")).strip().strip('"')
    if not named or named.upper() == "NONE":
        return None
    # Exact first, because that is what the harness does - a name the model
    # copied out of the list resolves to one rectangle and cannot be a wrong
    # coordinate. Fuzzy after, since a model sometimes paraphrases.
    for element in digest.elements:
        if element.name == named:
            return Target.from_element(element)
    element = digest.find(named)
    return Target.from_element(element) if element else None


def _vision(screenshot: Path, description: str, model) -> Target | None:
    from ..desktop.grounding import Target as _Target

    import base64

    try:
        data = base64.b64encode(screenshot.read_bytes()).decode("ascii")
    except OSError:
        return None
    prompt = (
        "Look at this Windows screenshot. The user said: "
        f'"{description}"\n\n'
        "Reply with the pixel coordinates of its centre as exactly: X,Y\n"
        "Nothing else. If you cannot find it, reply exactly: NONE")
    try:
        reply = model.invoke([{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{data}",
                           "detail": "low"}},
        ]}])
    except Exception:  # noqa: BLE001
        return None
    text = str(getattr(reply, "content", "")).strip()
    if "NONE" in text.upper():
        return None
    digits = "".join(c if c.isdigit() or c in ",.-" else " " for c in text)
    parts = [p for p in digits.replace(",", " ").split() if p.lstrip("-").isdigit()]
    if len(parts) < 2:
        return None
    x, y = int(parts[0]), int(parts[1])
    # A point, not a region: the baseline is scored on where it says to click,
    # so it gets a small box around that, not a generous one.
    from ..desktop.grounding import Source

    return _Target(left=x - 8, top=y - 8, right=x + 8, bottom=y + 8,
                   name=description, source=Source.VISION)


class _SavedShot:
    """A label's screenshot, shaped like the live one.

    `ComputerUseGrounding` takes whatever `capture_screens()` returns, so
    replaying a labelled set means handing it the same shape with the scale
    and origin recorded at labelling time. Without those the model's IMAGE
    coordinates cannot be compared with a point marked in SCREEN coordinates,
    and the whole score is off by the downscale factor.
    """

    class _Monitor:
        def __init__(self, left, top):
            self.left, self.top = left, top

    def __init__(self, image, scale, origin):
        self.image = image
        self.scale = scale or 1.0
        self.monitor = self._Monitor(*(origin or (0, 0)))


def _computer_use(label, place) -> Target | None:
    """Ground through the computer tool, from the saved picture."""
    from PIL import Image

    from ..desktop.computeruse import ComputerUseGrounding

    try:
        image = Image.open(place / label.screenshot_file)
    except OSError:
        return None
    shot = _SavedShot(image, getattr(label, "scale", 1.0),
                      tuple(getattr(label, "origin", (0, 0))))
    return ComputerUseGrounding(frozen=shot).locate(label.description)


def run(labels, strategies=("uia", "computer-use")) -> HeldOutReport:
    """Replay every strategy over a labelled set."""
    report = HeldOutReport(labels=len(labels))
    if not labels:
        return report

    model = None
    if "uia+model" in strategies or "vision" in strategies:
        from langchain_openai import ChatOpenAI

        from ..config import openai_api_key

        model = ChatOpenAI(model=MODEL, api_key=openai_api_key(),
                           max_completion_tokens=40)

    from .label import folder

    place = folder()
    for label in labels:
        digest = (_rebuild(place / label.digest_file)
                  if label.digest_file else None)
        in_digest = bool(label.nearest_uia_name)

        for name in strategies:
            started = time.perf_counter()
            target: Target | None = None
            if name == "uia" and digest is not None:
                target = _uia_plain(digest, label.description)
            elif name == "uia+model" and digest is not None and model is not None:
                target = _uia_with_model(digest, label.description, model)
            elif name == "vision" and label.screenshot_file and model is not None:
                target = _vision(place / label.screenshot_file,
                                 label.description, model)
            elif name == "computer-use" and label.screenshot_file:
                target = _computer_use(label, place)
            milliseconds = (time.perf_counter() - started) * 1000

            point = tuple(label.point)
            report.outcomes.append(Outcome(
                description=label.description, strategy=name,
                hit=bool(target) and _contains(target, point),
                distance=_distance(target, point) if target else None,
                milliseconds=milliseconds, answered=target is not None,
                app=label.app, in_digest=in_digest))
    return report
