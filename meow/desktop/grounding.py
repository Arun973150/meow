"""Grounding - Phase 1.2.

**Where is the thing the user named?** Three answers behind one interface, which
is invariant 3 and the reason the ablation in 04-evaluation.md is possible
without maintaining two codebases.

    VisionGrounding    screenshot -> model guesses (x, y)     [the baseline]
    StrictVisionGrounding  the same, asked for coordinates ONLY  [control]
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
    """Ask the accessibility tree. Exact or nothing.

    `frozen` pins it to one digest instead of re-reading. The evaluation needs
    that: a vision call takes ~2.7 seconds, and a live window moves underneath
    the experiment while it runs. Measured, that alone dropped UIA from 6/6 to
    3/6 - not because it failed, but because it was being asked about controls
    that had scrolled away since the ground truth was recorded.
    """

    def __init__(self, frozen: WindowDigest | None = None) -> None:
        self.last_digest: WindowDigest | None = frozen
        self._frozen = frozen

    @property
    def name(self) -> str:
        return "uia"

    def locate(self, description: str) -> Target | None:
        digest = self._frozen if self._frozen is not None else digest_foreground()
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

    def __init__(self, mind, capture, frozen=None) -> None:
        # `mind` answers and `capture` returns screenshots. Injected rather than
        # imported so the evaluation can drive this without a microphone.
        self._mind = mind
        self._capture = capture
        # Same reason as UIAGrounding: in an experiment both strategies must be
        # asked about the same screen, or the comparison measures how fast the
        # window changed rather than how well either one located anything.
        self._frozen = frozen

    @property
    def name(self) -> str:
        return "vision"

    def locate(self, description: str) -> Target | None:
        shots = self._frozen if self._frozen is not None else self._capture()
        if not shots:
            return None

        if self._frozen is not None:
            # The unchanged-screen optimisation must not run in an experiment.
            # A frozen screenshot is byte-identical on every task, so the
            # deduplicator skipped the image for five of six tasks and the
            # baseline was asked to locate controls with no picture at all -
            # scoring 0/6, for the wrong reason entirely. A handicapped
            # baseline proves nothing, which is the whole argument for
            # reproducing Clicky's method faithfully in the first place.
            self._mind.screen.forget()

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


class StrictVisionGrounding:
    """The vision baseline, asked for coordinates and nothing else.

    A CONTROL CONDITION, not a strategy anyone would ship. It exists to answer
    the first objection anyone should raise at `VisionGrounding` scoring zero:
    that the baseline was not really being measured because it kept declining
    to answer.

    That objection was real. Measured on VS Code, the conversational prompt
    produced a coordinate tag on ONE of eight tasks - the model said "it is
    over here" and emitted nothing. A strategy that answers one time in eight
    cannot be said to have been tested.

    So this strips everything else away: no personality, no spoken sentence, no
    option to decline. Reply with a tag or nothing, guess if unsure. That took
    tag emission from 1/8 to 8/8 - and the hit rate stayed at zero. Which is
    the point. The baseline is not losing because it refuses to play; it is
    losing because it cannot say where a control is to within its own bounds.
    """

    PROMPT = (
        "You locate controls in a screenshot. Reply with NOTHING but a tag:"
        + chr(10) + "    [POINT:x,y]" + chr(10) +
        "x and y are pixel coordinates in the attached image, which is "
        "{width} wide and {height} tall, with 0,0 at the top left. Point at "
        "the CENTRE of the control. You must always answer with a tag - guess "
        "your best if you are unsure. Never write anything else."
    )

    def __init__(self, mind, capture, frozen=None, model: str = "gpt-4o-mini"):
        self._mind = mind
        self._capture = capture
        self._frozen = frozen
        self._model = model
        self._client = None

    @property
    def name(self) -> str:
        return "vision-strict"

    def _chat(self):
        if self._client is None:
            from langchain_openai import ChatOpenAI

            from ..config import openai_api_key

            # Few tokens on purpose: the reply is a tag. A long budget invites
            # the model to explain itself, which is the behaviour being
            # controlled for.
            self._client = ChatOpenAI(model=self._model, max_tokens=40,
                                      api_key=openai_api_key(),
                                      stream_usage=True)
        return self._client

    def locate(self, description: str) -> Target | None:
        import re

        from langchain_core.messages import HumanMessage, SystemMessage

        from .pointing import Point

        shots = self._frozen if self._frozen is not None else self._capture()
        if not shots:
            return None
        shot = shots[0]

        # The deduplicator must not run here: a frozen screenshot is identical
        # every task, and skipping the image would measure a model with no
        # picture. That mistake has already cost this evaluation one wrong
        # result; see docs/04-evaluation.md.
        self._mind.screen.forget()
        attachment = self._mind.screen.attach(f"point at {description}", shot)
        if not attachment.data_url:
            return None

        prompt = self.PROMPT.format(width=shot.image.width,
                                    height=shot.image.height)
        reply = self._chat().invoke([
            SystemMessage(prompt),
            HumanMessage([
                {"type": "text", "text": "Point at: " + description},
                {"type": "image_url",
                 "image_url": {"url": attachment.data_url,
                               "detail": attachment.detail}},
            ]),
        ])

        usage = getattr(reply, "usage_metadata", None)
        if usage:
            # Counted into the same budget as everything else, so the cost of
            # the control condition is visible rather than hidden.
            self._mind.screen.budget.record(usage.get("input_tokens", 0),
                                            usage.get("output_tokens", 0))

        found = re.search(r"\[POINT:\s*(-?\d+)\s*,\s*(-?\d+)",
                          str(reply.content))
        if not found:
            return None

        # Through the same conversion the real path uses. An image coordinate
        # is not a screen coordinate - the shot may be one monitor of several,
        # and a virtual desktop goes negative left of the primary.
        screen = Point(x=int(found.group(1)), y=int(found.group(2)),
                       label=description, screen=1).to_screen(shots)
        if screen is None:
            return None
        return Target.from_point(screen[0], screen[1], description)


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
