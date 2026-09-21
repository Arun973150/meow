"""Grounding - Phase 1.2.

**Where is the thing the user named?** Three answers behind one interface, which
is invariant 3 and the reason the ablation in 04-evaluation.md is possible
without maintaining two codebases.

    VisionGrounding    screenshot -> model guesses (x, y)     [the baseline]
    UIAGrounding       accessibility tree -> exact rectangle
    HybridGrounding    ask the tree, fall back to pixels

The difference between the first two is not accuracy alone. A vision guess is a
point; a UIA hit is a *rectangle with a handle*, which means it can be invoked
without the pointer going anywhere, on a window that is not even in front. The
evaluation measures hit rate, but the capability gap is larger than the number.

`HybridGrounding` is the one that ships. It is not a fallback wrapper around a
weak primary - the regime decides, and both paths are real.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .uia import Element, Regime, WindowDigest, digest_foreground


class Source(Enum):
    UIA = "uia"
    VISION = "vision"


@dataclass(frozen=True)
class Target:
    """Something located on screen, and how we know where it is."""

    left: int
    top: int
    right: int
    bottom: int
    name: str
    role: str
    source: Source
    # Only UIA targets carry one. It is what allows an element to be operated
    # without moving the mouse at all.
    element: Element | None = None

    @property
    def centre(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    @property
    def is_exact(self) -> bool:
        """True when the coordinates came from Windows rather than a guess."""
        return self.source is Source.UIA

    @classmethod
    def from_element(cls, element: Element) -> "Target":
        return cls(
            left=element.left, top=element.top,
            right=element.right, bottom=element.bottom,
            name=element.name, role=element.role,
            source=Source.UIA, element=element,
        )

    @classmethod
    def from_point(cls, x: int, y: int, name: str = "",
                   radius: int = 12) -> "Target":
        """A vision guess, given a nominal box so it has the same shape.

        The radius is a fiction - the model returned a point, not an extent -
        but the ablation compares like with like, and the caller should not
        need to know which strategy produced a target.
        """
        return cls(
            left=x - radius, top=y - radius,
            right=x + radius, bottom=y + radius,
            name=name, role="", source=Source.VISION,
        )

    def describe(self) -> str:
        x, y = self.centre
        how = "exact" if self.is_exact else "estimated"
        return f'{self.role or "target"} "{self.name}" at {x},{y} ({how})'


class Grounding(Protocol):
    """Locate something by description. None means not found."""

    def locate(self, description: str) -> Target | None: ...
    @property
    def name(self) -> str: ...


class UIAGrounding:
    """Ask the accessibility tree. Exact or nothing."""

    def __init__(self) -> None:
        self.last_digest: WindowDigest | None = None

    @property
    def name(self) -> str:
        return "uia"

    def locate(self, description: str) -> Target | None:
        digest = digest_foreground()
        self.last_digest = digest
        if digest is None or digest.regime is Regime.EMPTY:
            return None
        element = digest.find(description)
        return Target.from_element(element) if element else None


class VisionGrounding:
    """Ask the model to point at it. This is the Clicky baseline.

    Reproduced faithfully rather than weakened, because it is the control
    condition. A baseline that has been quietly handicapped proves nothing.
    """

    def __init__(self, mind, capture) -> None:
        # `mind` answers and `capture` returns screenshots. Injected rather than
        # imported so the evaluation can drive this without a microphone.
        self._mind = mind
        self._capture = capture

    @property
    def name(self) -> str:
        return "vision"

    def locate(self, description: str) -> Target | None:
        shots = self._capture()
        if not shots:
            return None

        # Consume the generator: the tag arrives at the end of the reply, so
        # the answer is only complete once the stream is.
        list(self._mind.answer(f"point at {description}", shots[0]))

        point = self._mind.last_point
        if point is None:
            return None
        screen = point.to_screen(shots)
        if screen is None:
            return None
        return Target.from_point(screen[0], screen[1],
                                 point.label or description)


class HybridGrounding:
    """The tree where it works, pixels where it does not.

    Not a fallback wrapper. The regime decides which is authoritative, and a
    vision answer is a real answer rather than an apology for a failed one.
    """

    def __init__(self, vision: VisionGrounding | None = None) -> None:
        self.uia = UIAGrounding()
        self.vision = vision
        self.last_source: Source | None = None
        self.last_regime: Regime | None = None

    @property
    def name(self) -> str:
        return "hybrid"

    def locate(self, description: str) -> Target | None:
        target = self.uia.locate(description)
        digest = self.uia.last_digest
        self.last_regime = digest.regime if digest else None

        if target is not None:
            self.last_source = Source.UIA
            return target

        # The tree answered, and the answer was that this thing is not in it -
        # a canvas, a custom-drawn control, a game. Pixels are the only route.
        if self.vision is not None:
            target = self.vision.locate(description)
            if target is not None:
                self.last_source = Source.VISION
                return target

        self.last_source = None
        return None
