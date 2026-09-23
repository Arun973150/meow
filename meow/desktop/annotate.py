"""Drawing on the screen, so teaching can point at things properly.

A cursor glide says "there". Teaching needs more than that: circle the region
of a diagram, trace the arc a knight takes, ring two things and draw a line
between them, put a box round the square a piece should move to. Those are
different marks, and a rectangle cannot express most of them.

**Curves are the reason this is not just three primitives.** A bond path in a
molecule, the sweep of a knight's move, an arrow that bends around a panel to
reach what is behind it - none of those are a straight line, and drawing them
as two segments looks like a mistake rather than a gesture. PIL has no curve,
so they are sampled from a Bezier here.

**Specified the way somebody would say it, not the way a graphics library
wants it.** A model asked for four Bezier control points will produce
nonsense. Asked to draw a curve from here to there that bows upward, it can
do that - so `curve()` takes two points and a bow, and works the control
point out.

**The layer is EXCLUDED FROM CAPTURE.** Invariant 7, and it matters twice as
much here: the grounding model reads a screenshot, and marks drawn on the
screen would appear in the next one. The cat would then be pointing at its
own arrows.

**Every mark expires.** A teaching overlay that accumulates is a window
somebody has to clean up, and nobody ever does.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

# Drawn at this multiple and shrunk back down. PIL has no anti-aliasing for
# lines or arcs, and a jagged circle round somebody's work looks broken rather
# than deliberate. Two is enough; four costs four times the pixels for a
# difference nobody sees.
SUPERSAMPLE = 2

# Points along a sampled curve. Sixty is smooth at any size a screen offers
# and cheap - this is arithmetic, not rendering.
CURVE_STEPS = 60

# How long a mark stays before it fades, unless it is given its own lifetime.
DEFAULT_SECONDS = 6.0

# The last fraction of a mark's life spent fading out. An annotation that
# vanishes between one frame and the next reads as a glitch.
FADE_FRACTION = 0.25

# Readable on a dark timeline and on a white page both. Alpha is applied
# separately, so these are opaque.
# Opacity is rounded to this before deciding whether to redraw. A full-screen
# canvas at 2x supersample is ~9 million pixels and takes 34ms to render, so
# a fade animated continuously would redraw sixty times a second and halve the
# cat's frame rate. Quantised, a fade costs five redraws and looks the same.
#
# A time-based throttle does NOT work here and was tried first: one render
# takes longer than the interval, so "is it due yet" is always true and
# nothing is saved. Redrawing only when the picture would actually differ is
# the only thing that helps.
OPACITY_STEP = 0.2

INK = (255, 92, 48)
ACCENT = (64, 196, 255)

# Point size of a label, before supersampling. PIL's default is a BITMAP font
# that does not scale, so a label drawn at 2x and shrunk back came out at half
# size and unreadable - the text has to be asked for at the supersampled size,
# from a real outline font.
LABEL_POINTS = 17
_FONTS = ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf")


def _font(scale: int):
    """A scalable font at the supersampled size, or the bitmap fallback."""
    for name in _FONTS:
        try:
            return ImageFont.truetype(name, LABEL_POINTS * scale)
        except OSError:
            continue
    return ImageFont.load_default()


@dataclass
class Mark:
    """One thing drawn on the screen, and when it stops being drawn."""

    kind: str
    points: list                      # screen coordinates
    colour: tuple = INK
    width: int = 4
    text: str = ""
    bow: float = 0.0
    seconds: float = DEFAULT_SECONDS
    born: float = field(default_factory=time.monotonic)

    @property
    def age(self) -> float:
        return time.monotonic() - self.born

    @property
    def expired(self) -> bool:
        return self.age >= self.seconds

    @property
    def opacity(self) -> float:
        """1.0, then a fade at the end rather than a disappearance."""
        remaining = self.seconds - self.age
        fading = self.seconds * FADE_FRACTION
        if remaining >= fading or fading <= 0:
            return 1.0
        return max(0.0, remaining / fading)


def bezier(points: list, steps: int = CURVE_STEPS) -> list:
    """Sample a Bezier of any order. Two points is a straight line.

    De Casteljau rather than the cubic formula, so the same code draws a
    quadratic, a cubic, or the six-point path somebody traced by hand.
    """
    if len(points) < 2:
        return list(points)
    sampled = []
    for step in range(steps + 1):
        t = step / steps
        current = list(points)
        while len(current) > 1:
            current = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                       for a, b in zip(current, current[1:])]
        sampled.append(current[0])
    return sampled


def bowed(start: tuple, end: tuple, bow: float) -> list:
    """A curve from start to end, bulging sideways by `bow`.

    `bow` is a fraction of the distance between the two points, positive one
    way and negative the other, so 0.3 is a gentle arc and 0 is a straight
    line. This exists because "draw a curve from the knight to e5, bowing
    upward" is a thing somebody can say and four control points is not.
    """
    if not bow:
        return [start, end]
    middle = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    # Perpendicular, normalised, scaled by the bow.
    control = (middle[0] - dy / length * length * bow,
               middle[1] + dx / length * length * bow)
    return [start, control, end]


class Sketch:
    """The marks currently on screen."""

    def __init__(self) -> None:
        self.marks: list[Mark] = []

    # --- the vocabulary ---------------------------------------------------

    def arrow(self, start, end, bow: float = 0.0, **kw) -> Mark:
        """An arrow, straight or curved. The head follows the curve's end."""
        return self._add(Mark("arrow", [tuple(start), tuple(end)],
                              bow=bow, **kw))

    def line(self, start, end, bow: float = 0.0, **kw) -> Mark:
        return self._add(Mark("line", [tuple(start), tuple(end)], bow=bow, **kw))

    def curve(self, points, **kw) -> Mark:
        """A curve through any number of control points."""
        return self._add(Mark("curve", [tuple(p) for p in points], **kw))

    def path(self, points, **kw) -> Mark:
        """Freehand: the points are followed exactly, not smoothed."""
        return self._add(Mark("path", [tuple(p) for p in points], **kw))

    def box(self, left, top, right, bottom, **kw) -> Mark:
        return self._add(Mark("box", [(left, top), (right, bottom)], **kw))

    def circle(self, centre, radius: int, **kw) -> Mark:
        return self._add(Mark("circle", [tuple(centre), (radius, radius)], **kw))

    def ellipse(self, left, top, right, bottom, **kw) -> Mark:
        return self._add(Mark("ellipse", [(left, top), (right, bottom)], **kw))

    def highlight(self, left, top, right, bottom, **kw) -> Mark:
        """A translucent wash over a region, for "look at this part"."""
        return self._add(Mark("highlight", [(left, top), (right, bottom)], **kw))

    def label(self, at, text: str, **kw) -> Mark:
        return self._add(Mark("label", [tuple(at)], text=text, **kw))

    # --- housekeeping -----------------------------------------------------

    def _add(self, mark: Mark) -> Mark:
        self.marks.append(mark)
        return mark

    def clear(self) -> None:
        self.marks.clear()

    def prune(self) -> bool:
        """Drop expired marks. True when something went."""
        before = len(self.marks)
        self.marks = [mark for mark in self.marks if not mark.expired]
        return len(self.marks) != before

    @property
    def empty(self) -> bool:
        return not self.marks

    # --- rendering --------------------------------------------------------

    def render(self, width: int, height: int,
               origin: tuple = (0, 0)) -> Image.Image:
        """An RGBA image of every live mark, in screen coordinates.

        `origin` is the overlay's top-left on the virtual desktop, so marks
        given in screen coordinates land in the right place on a second
        monitor - whose coordinates are negative when it sits to the left.
        """
        scale = SUPERSAMPLE
        canvas = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
        pen = ImageDraw.Draw(canvas, "RGBA")

        def place(point):
            return ((point[0] - origin[0]) * scale,
                    (point[1] - origin[1]) * scale)

        for mark in self.marks:
            alpha = int(255 * mark.opacity)
            if alpha <= 0:
                continue
            colour = (*mark.colour, alpha)
            thickness = max(1, mark.width * scale)
            self._draw(pen, mark, colour, thickness, place, scale)

        return canvas.resize((width, height), Image.LANCZOS)

    def _draw(self, pen, mark, colour, thickness, place, scale) -> None:
        kind = mark.kind

        if kind in ("line", "arrow"):
            control = bowed(mark.points[0], mark.points[1], mark.bow)
            sampled = [place(p) for p in bezier(control)]
            pen.line(sampled, fill=colour, width=thickness, joint="curve")
            if kind == "arrow":
                self._head(pen, sampled, colour, thickness)
            return

        if kind in ("curve", "path"):
            points = [place(p) for p in (
                bezier(mark.points) if kind == "curve" else mark.points)]
            if len(points) >= 2:
                pen.line(points, fill=colour, width=thickness, joint="curve")
            return

        if kind == "box":
            (left, top), (right, bottom) = (place(p) for p in mark.points)
            pen.rounded_rectangle([left, top, right, bottom],
                                  radius=6 * scale, outline=colour,
                                  width=thickness)
            return

        if kind == "circle":
            centre = place(mark.points[0])
            radius = mark.points[1][0] * scale
            pen.ellipse([centre[0] - radius, centre[1] - radius,
                         centre[0] + radius, centre[1] + radius],
                        outline=colour, width=thickness)
            return

        if kind == "ellipse":
            (left, top), (right, bottom) = (place(p) for p in mark.points)
            pen.ellipse([left, top, right, bottom], outline=colour,
                        width=thickness)
            return

        if kind == "highlight":
            (left, top), (right, bottom) = (place(p) for p in mark.points)
            wash = (*mark.colour, int(colour[3] * 0.22))
            pen.rounded_rectangle([left, top, right, bottom],
                                  radius=8 * scale, fill=wash,
                                  outline=colour, width=max(1, thickness // 2))
            return

        if kind == "label":
            x, y = place(mark.points[0])
            font = _font(scale)
            # A plate behind it, because a label is unreadable on exactly the
            # busy screenshot somebody most needs it explained on.
            box = pen.textbbox((x, y), mark.text, font=font)
            pad = 5 * scale
            pen.rounded_rectangle(
                [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad],
                radius=5 * scale, fill=(16, 16, 20, int(colour[3] * 0.82)))
            pen.text((x, y), mark.text, fill=colour, font=font)

    @staticmethod
    def _head(pen, sampled, colour, thickness) -> None:
        """An arrowhead aligned with the curve where it ends.

        Taken from the last two SAMPLED points rather than from start and end,
        so a bowed arrow's head follows the bend instead of pointing at where
        a straight line would have gone.
        """
        if len(sampled) < 2:
            return
        tip = sampled[-1]
        before = sampled[-2]
        angle = math.atan2(tip[1] - before[1], tip[0] - before[0])
        size = max(10, thickness * 3.5)
        spread = math.radians(26)
        pen.polygon([
            tip,
            (tip[0] - size * math.cos(angle - spread),
             tip[1] - size * math.sin(angle - spread)),
            (tip[0] - size * math.cos(angle + spread),
             tip[1] - size * math.sin(angle + spread)),
        ], fill=colour)


class Board:
    """A full-screen layer the marks are drawn on.

    Its own overlay rather than the cat's: the cat's window is small, follows
    the pointer and goes home, and a mark anchored to it would wander off the
    thing it is pointing at.

    Click-through and excluded from capture, like every other overlay here.
    The exclusion matters more for this one than for the cat: the grounding
    model reads a screenshot to decide where to point, and marks that appeared
    in it would have the cat pointing at its own arrows.
    """

    def __init__(self, monitor=None) -> None:
        from ..platform.monitors import get_virtual_desktop
        from ..platform.overlay import Bounds, Overlay

        if monitor is None:
            monitor = get_virtual_desktop().primary
        self.monitor = monitor
        self.origin = (monitor.left, monitor.top)
        self.sketch = Sketch()
        self._overlay = Overlay(
            Bounds(left=monitor.left, top=monitor.top,
                   width=monitor.width, height=monitor.height),
            exclude_from_capture=True, click_through=True)
        self._showing = False
        self._blank = True
        self._last_render = 0.0
        self._last_signature = None

    def draw(self) -> None:
        """Push the current marks. Cheap when nothing is live.

        Safe to call every frame: it returns immediately when the board is
        empty, and throttles when it is not.
        """
        from ..cat import rgba_to_premultiplied_bgra

        self.sketch.prune()
        if self.sketch.empty:
            # An empty board is hidden rather than drawn transparent. A
            # layered window still composites every frame, and there is no
            # reason to pay for that to show nothing.
            if self._showing:
                self._overlay.hide()
                self._showing = False
            self._blank = True
            return

        # Re-render when the marks changed, or on the throttle while anything
        # is fading. Without this the loop rebuilds 37MB of pixels sixty times
        # a second to animate an arrow nobody is watching that closely.
        signature = tuple(
            (id(mark), round(mark.opacity / OPACITY_STEP))
            for mark in self.sketch.marks)
        if signature == self._last_signature and self._showing:
            return
        self._last_signature = signature

        image = self.sketch.render(self.monitor.width, self.monitor.height,
                                   self.origin)
        self._overlay.draw(rgba_to_premultiplied_bgra(image))
        if not self._showing:
            self._overlay.show()
            self._showing = True
        self._blank = False

    def clear(self) -> None:
        self.sketch.clear()
        self.draw()

    def close(self) -> None:
        try:
            self._overlay.close()
        except Exception:  # noqa: BLE001 - teardown must not raise
            pass
