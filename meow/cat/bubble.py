"""The speech bubble.

**Voice is the primary channel. This is a supporting cue, not a transcript.**

That distinction is the whole design constraint. Meow answers out loud, in
sentences written for the ear - see the voice conventions in AGENTS.md. If the
bubble carried the full reply, three things would go wrong at once: the user
would read instead of listen, which throws away the latency advantage of
speaking the first sentence before the second is written; a wall of text would
cover the very screen they are working on; and the cat would stop being a
companion and become a chat window with ears.

So the text is capped hard, at MAX_CHARACTERS and MAX_LINES, and truncated with
an ellipsis rather than allowed to grow. The cap is the feature.

What it is genuinely for:

- a short confirmation the user can glance at - "opening word"
- names and numbers that are painful to hear and easy to read - a filename, a
  figure, a street address
- accessibility: someone deaf or hard of hearing, or in a loud room, or with
  the volume down in a meeting, gets the gist without the audio

The bubble is a filled card rather than line art like the cat, because text over
arbitrary desktop content is unreadable without a background behind it.
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Hard caps. A bubble that grows to fit its content is a chat window; this one
# truncates instead, and the truncation is visible so nobody mistakes it for the
# whole answer.
MAX_CHARACTERS = 90
MAX_LINES = 3

CORNER_RADIUS = 10
PADDING_X = 11
PADDING_Y = 8
TAIL_WIDTH = 13
TAIL_HEIGHT = 8

# The thinking animation.
THINKING_DOTS = 3
THINKING_DOT_SIZE = 6
THINKING_DOT_GAP = 5
THINKING_DOT_RISE = 3.0

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
    r"C:\Windows\Fonts\arial.ttf",
)


@dataclass(frozen=True)
class BubblePalette:
    fill: tuple[int, int, int, int] = (252, 252, 254, 244)
    border: tuple[int, int, int, int] = (26, 26, 30, 235)
    text: tuple[int, int, int, int] = (26, 26, 30, 255)

    @classmethod
    def for_background(cls, luminance: float) -> "BubblePalette":
        """Match the cat: dark card on a dark desktop, light on a light one.

        Same threshold as CatPalette.for_background, so the bubble and the cat
        never end up in opposite schemes on the same screen.
        """
        if luminance < 0.42:
            return cls(
                fill=(30, 30, 34, 244),
                border=(236, 236, 240, 225),
                text=(240, 240, 244, 255),
            )
        return cls()


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    # Pillow's built-in bitmap font. Ugly and fixed-size, but the bubble still
    # renders and says something rather than crashing on an unusual install.
    return ImageFont.load_default()


def clamp_text(text: str) -> str:
    """Cut the text down to what a glance can take in.

    Truncates on a word boundary where it can, because a bubble ending
    mid-word looks like a rendering bug rather than a deliberate summary.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= MAX_CHARACTERS:
        return collapsed

    cut = collapsed[:MAX_CHARACTERS]
    last_space = cut.rfind(" ")
    if last_space > MAX_CHARACTERS * 0.6:
        cut = cut[:last_space]
    return cut.rstrip(" ,.;:") + "\u2026"


class BubbleRenderer:
    """Draws a small speech bubble sized to its text."""

    def __init__(self, font_size: int = 13, max_width: int = 230,
                 palette: BubblePalette | None = None) -> None:
        self.font_size = font_size
        self.max_width = max_width
        self.palette = palette or BubblePalette()
        self.font = _load_font(font_size)
        # One throwaway image to measure against. Pillow needs a draw context
        # to compute text extents, and making one per frame is wasteful.
        self._measuring_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    def wrap(self, text: str) -> list[str]:
        """Break the text into at most MAX_LINES that fit max_width."""
        clamped = clamp_text(text)
        if not clamped:
            return []

        # Estimate characters per line from the average glyph width, then let
        # textwrap do the word breaking. Close enough at this size, and far
        # cheaper than measuring every candidate break.
        average_width = max(
            1.0, self._measuring_draw.textlength("abcdefghij", font=self.font) / 10
        )
        characters_per_line = max(8, int(self.max_width / average_width))

        lines = textwrap.wrap(clamped, width=characters_per_line)[:MAX_LINES]
        if lines and len(textwrap.wrap(clamped, width=characters_per_line)) > MAX_LINES:
            lines[-1] = lines[-1].rstrip(" ,.;:") + "\u2026"
        return lines

    def measure(self, text: str) -> tuple[int, int]:
        """Pixel size of the bubble this text would produce, tail included."""
        lines = self.wrap(text)
        if not lines:
            return (0, 0)

        line_height = self.font_size + 4
        widest = max(
            self._measuring_draw.textlength(line, font=self.font) for line in lines
        )
        return (
            int(widest) + PADDING_X * 2 + 2,
            line_height * len(lines) + PADDING_Y * 2 + TAIL_HEIGHT + 2,
        )

    def measure_thinking(self) -> tuple[int, int]:
        """The thinking bubble is a fixed size - it holds three dots."""
        line_height = self.font_size + 4
        return (THINKING_DOTS * (THINKING_DOT_SIZE + THINKING_DOT_GAP)
                + PADDING_X * 2, line_height + PADDING_Y * 2 + TAIL_HEIGHT + 2)

    def render_thinking(self, phase: float, alpha: float = 1.0,
                        tail_on_right: bool = True) -> Image.Image | None:
        """Three dots rising and falling in sequence.

        Planning takes a second or two, and an empty bubble during it reads as
        nothing happening. Dots rather than a spinner: a spinner says "wait",
        and three dots say "it is composing something", which is what is
        actually going on.

        Each dot is offset a third of a cycle from the last, so the movement
        travels left to right instead of all three bouncing together.
        """
        if alpha <= 0.01:
            return None

        width, height = self.measure_thinking()
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        body_bottom = height - TAIL_HEIGHT - 1
        self._draw_body(draw, width, body_bottom, height, alpha, tail_on_right)

        centre_y = body_bottom / 2
        for index in range(THINKING_DOTS):
            # A third of a cycle between neighbours.
            offset = phase * 6.0 - index * (2.0 * math.pi / 3.0)
            lift = math.sin(offset)
            # Dots at the bottom of their arc fade slightly, which reads as
            # depth and stops the row looking like it is sliding sideways.
            fade = 0.55 + 0.45 * (lift * 0.5 + 0.5)
            x = PADDING_X + index * (THINKING_DOT_SIZE + THINKING_DOT_GAP)
            y = centre_y - lift * THINKING_DOT_RISE - THINKING_DOT_SIZE / 2
            draw.ellipse(
                [x, y, x + THINKING_DOT_SIZE, y + THINKING_DOT_SIZE],
                fill=self._faded(self.palette.text, alpha * fade),
            )
        return image

    def _draw_body(self, draw, width, body_bottom, height, alpha,
                   tail_on_right) -> None:
        """The card and its tail, shared by text and thinking bubbles."""
        fill = self._faded(self.palette.fill, alpha)
        border = self._faded(self.palette.border, alpha)

        draw.rounded_rectangle(
            [0, 0, width - 1, body_bottom],
            radius=CORNER_RADIUS, fill=fill, outline=border, width=1,
        )
        tail_x = width - CORNER_RADIUS - TAIL_WIDTH if tail_on_right else CORNER_RADIUS
        tip = (tail_x + (TAIL_WIDTH // 2 if tail_on_right else 0), height - 1)
        draw.polygon(
            [(tail_x, body_bottom - 1), (tail_x + TAIL_WIDTH, body_bottom - 1), tip],
            fill=fill,
        )
        draw.line([(tail_x, body_bottom - 1), tip], fill=border, width=1)
        draw.line([tip, (tail_x + TAIL_WIDTH, body_bottom - 1)], fill=border, width=1)

    def render(self, text: str, alpha: float = 1.0,
               tail_on_right: bool = True) -> Image.Image | None:
        """The bubble, RGBA. None when there is nothing to say.

        The tail points down toward the cat, on whichever side the cat is - so
        the bubble can sit above-left of a cat near the right screen edge and
        still clearly belong to it.
        """
        lines = self.wrap(text)
        if not lines or alpha <= 0.01:
            return None

        width, height = self.measure(text)
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        body_bottom = height - TAIL_HEIGHT - 1
        fill = self._faded(self.palette.fill, alpha)
        border = self._faded(self.palette.border, alpha)

        draw.rounded_rectangle(
            [0, 0, width - 1, body_bottom],
            radius=CORNER_RADIUS, fill=fill, outline=border, width=1,
        )

        tail_x = width - CORNER_RADIUS - TAIL_WIDTH if tail_on_right else CORNER_RADIUS
        draw.polygon(
            [
                (tail_x, body_bottom - 1),
                (tail_x + TAIL_WIDTH, body_bottom - 1),
                (tail_x + (TAIL_WIDTH // 2 if tail_on_right else 0), height - 1),
            ],
            fill=fill,
        )
        # Only the two sloping edges get a border. Stroking the top as well
        # would draw a line straight across the join and make the tail look
        # stuck on rather than part of the bubble.
        tip = (tail_x + (TAIL_WIDTH // 2 if tail_on_right else 0), height - 1)
        draw.line([(tail_x, body_bottom - 1), tip], fill=border, width=1)
        draw.line([tip, (tail_x + TAIL_WIDTH, body_bottom - 1)], fill=border, width=1)

        line_height = self.font_size + 4
        text_colour = self._faded(self.palette.text, alpha)
        for index, line in enumerate(lines):
            draw.text(
                (PADDING_X, PADDING_Y + index * line_height),
                line, font=self.font, fill=text_colour,
            )
        return image

    @staticmethod
    def _faded(colour: tuple[int, int, int, int], alpha: float):
        clamped = max(0.0, min(1.0, alpha))
        return colour[:3] + (int(colour[3] * clamped),)


class BubbleState:
    """When the bubble is showing, and how faded in it is.

    Holds a fade rather than toggling visibility, because a bubble that pops in
    and out draws the eye far harder than one that arrives. The cat is meant to
    be ignorable.
    """

    FADE_IN_SECONDS = 0.18
    FADE_OUT_SECONDS = 0.28

    def __init__(self) -> None:
        self.text = ""
        self.thinking = False
        self._alpha = 0.0
        self._fading_out = False
        self._hide_at: float | None = None

    @property
    def alpha(self) -> float:
        return self._alpha

    @property
    def visible(self) -> bool:
        return self._alpha > 0.01

    def think(self, now: float) -> None:
        """Show the dots. Stays until something is said or it is dismissed.

        No timeout, unlike a spoken line: the dots mean "still working", and a
        thinking bubble that expires while the work continues is worse than no
        bubble at all.
        """
        self.thinking = True
        self.text = ""
        self._fading_out = False
        self._hide_at = None

    def say(self, text: str, now: float, seconds: float = 4.0) -> None:
        """Show a line, and schedule it to fade out.

        A bubble with no timeout is a bubble that is still on screen twenty
        minutes later, covering something.
        """
        self.text = text
        self.thinking = False
        self._fading_out = False
        self._hide_at = now + seconds

    def dismiss(self, now: float) -> None:
        self._hide_at = now

    def update(self, now: float, timestep: float) -> None:
        if self._hide_at is not None and now >= self._hide_at:
            self._fading_out = True

        if self._fading_out:
            self._alpha = max(0.0, self._alpha - timestep / self.FADE_OUT_SECONDS)
            if self._alpha <= 0.0:
                self.text = ""
                self.thinking = False
                self._hide_at = None
                self._fading_out = False
        elif self.text or self.thinking:
            self._alpha = min(1.0, self._alpha + timestep / self.FADE_IN_SECONDS)


def bubble_position(
    cat_left: int,
    cat_top: int,
    cat_width: int,
    bubble_width: int,
    bubble_height: int,
    monitor,
    gap: int = 2,
) -> tuple[int, int, bool]:
    """Where to put the bubble so its tail lands on the cat.

    Returns (left, top, tail_on_right). Sits above the cat by default and flips
    the tail to the other side rather than sliding the bubble away when it would
    run off the screen - the tail has to keep pointing at the cat, or the bubble
    reads as belonging to whatever else is nearby.
    """
    cat_center_x = cat_left + cat_width // 2
    tail_inset = CORNER_RADIUS + TAIL_WIDTH // 2

    left = cat_center_x - tail_inset
    tail_on_right = False
    if left + bubble_width > monitor.work_right:
        tail_on_right = True
        left = cat_center_x + tail_inset - bubble_width

    left = max(monitor.work_left,
               min(left, monitor.work_right - bubble_width))

    top = cat_top - bubble_height - gap
    if top < monitor.work_top:
        # No room above: tuck it below the cat instead. The tail then points
        # the wrong way, which is worse than a bubble half off the top of the
        # screen only if you cannot see it at all - so clamp rather than flip.
        top = monitor.work_top
    return int(left), int(top), tail_on_right
