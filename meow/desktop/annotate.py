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

**Marks are drawn, not plotted.** Perfectly circular circles and perfectly
straight lines read as a machine's output overlaid on somebody's work; a
slightly wobbly ring reads as a person having drawn round the thing. Two
strokes with a low-frequency sine offset along the path normal, which is the
Rough.js trick and costs arithmetic rather than rendering. `HAND_DRAWN = False`
turns it off, and the geometry underneath is unchanged either way - the wobble
is applied at stroke time, so nothing downstream has to know about it.

**Every mark expires, unless it is pinned.** A teaching overlay that
accumulates is a window somebody has to clean up, and nobody ever does. But a
walkthrough step can take a minute to follow, and a ring that faded after six
seconds left somebody looking at the place it used to be - so `seconds=PINNED`
stays until something clears it, and a walkthrough owns the clearing.

**Marks belong to GROUPS.** A walkthrough replaces the marks for step two
without touching the ones explaining the window; a spotlight for one step is
not the spotlight for the next. Without a group the only options were clearing
everything or waiting for a fade.
"""

from __future__ import annotations

import itertools
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

# A mark with this lifetime never expires. Zero rather than None so the field
# stays a float, and checked explicitly: `age >= 0` is true the instant a mark
# is born, so a naive comparison would expire a pinned mark immediately.
PINNED = 0.0

# --- the hand-drawn look -----------------------------------------------------
#
# Machine-perfect geometry overlaid on somebody's work reads as a screenshot
# annotation tool. A wobbly ring reads as a person having drawn round the
# thing, which is what this is pretending to be.
HAND_DRAWN = True

# Two passes, slightly apart, is what makes it read as pen rather than noise.
# One pass with a wobble just looks like a bad line.
SKETCH_PASSES = 2

# Sideways wobble, in pre-supersample pixels. Three is visible at arm's length
# and still lands inside the thing being circled; at eight the ring stops
# agreeing with the rectangle it came from.
WOBBLE_PIXELS = 3.0

# Wobbles along the length of the stroke. Low on purpose: per-point random
# offsets give a fuzzy line, and the hand-drawn look comes from a few long
# deviations rather than many short ones.
WOBBLE_CYCLES = 1.7

# Rings in a target, and how much smaller each one is than the last. Clicky
# calls these target rings and they are the clearest "here" available - a
# single circle has to be sized to the thing, and two concentric ones say
# "this point" regardless of what is under them.
TARGET_RINGS = 3
RING_STEP = 0.42

# How dark the screen goes outside a spotlight. Dimming the irrelevant and
# lighting the relevant is the one teaching cue this project did not have, and
# it is the one with evidence behind it: cued material beats uncued by about
# nine points. Too dark and it reads as a modal dialog over their work.
SHADOW = (8, 10, 16)
SHADOW_ALPHA = 150

# Radius of a numbered badge, before supersampling, and the size of its digit.
# Numbers are two jobs in one shape: "do these three in order" for a person,
# and the Set-of-Mark primitive for a model, which answers with an ID far
# better than it answers with a coordinate.
BADGE_RADIUS = 17
BADGE_POINTS = 19

# Height of the agent's drawn pointer, before supersampling. About the size
# of a real Windows cursor, so it reads as one rather than as a decoration.
CURSOR_SIZE = 16

# Point size of a label, before supersampling. PIL's default is a BITMAP font
# that does not scale, so a label drawn at 2x and shrunk back came out at half
# size and unreadable - the text has to be asked for at the supersampled size,
# from a real outline font.
LABEL_POINTS = 17
_FONTS = ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf")

# --- what is actually repainted ---------------------------------------------
#
# Only the rectangle the marks occupy, at 2x, rather than the whole screen.
# MEASURED, because the old comment about 34ms was wrong by an order of
# magnitude at this machine's real resolution:
#
#     2560x1600, one ring      alloc 14ms   draw 23ms   LANCZOS 266ms
#
# The downscale is the whole cost and it does not care how much was drawn - a
# 5120x3200 resize is nine million pixels of filtering whether the picture
# holds one ring or nine. Rendering the ring's own 150px box instead makes it
# arithmetic on 90,000 pixels, and the output is identical because everything
# outside the box was transparent anyway.
#
# `expired` and `opacity` already keep an idle board free; this is the cost of
# a board with something on it, which is every frame of a walkthrough.

# Added to every side of the box, in output pixels. It has to cover everything
# drawn OUTSIDE a mark's own geometry, or the fast path clips marks and the bug
# looks like a rendering fault rather than a wrong rectangle:
#
#     the stroke's half-width            width / 2
#     the hand-drawn wobble              WOBBLE_PIXELS
#     an arrowhead beyond the last point max(10, width * SUPERSAMPLE * 3.5)
#
# Computed per mark from its own width rather than fixed, since a thick arrow's
# head is 3.5 times its thickness. Four pixels on top, for the resize filter
# reaching a little past the edge of what was drawn.
MARK_MARGIN = 4


def _mark_margin(mark) -> float:
    """How far outside its own points a mark can paint, in output pixels."""
    head = max(10.0, mark.width * SUPERSAMPLE * 3.5) / SUPERSAMPLE
    return head + mark.width / 2.0 + WOBBLE_PIXELS + MARK_MARGIN


def _font(scale: int, points: int = LABEL_POINTS):
    """A scalable font at the supersampled size, or the bitmap fallback."""
    for name in _FONTS:
        try:
            return ImageFont.truetype(name, points * scale)
        except OSError:
            continue
    return ImageFont.load_default()


# Every mark gets one, so its wobble is the same on every re-render. `id()`
# would do until CPython reuses an address and two marks suddenly share a
# hand. A fade re-renders the same mark five times and it has to look like
# one line each time.
_seeds = itertools.count(1)


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
    # What this mark belongs to, so a walkthrough can replace the marks for
    # one step without clearing the ones explaining the window.
    group: str = ""
    born: float = field(default_factory=time.monotonic)
    seed: int = field(default_factory=lambda: next(_seeds))

    @property
    def age(self) -> float:
        return time.monotonic() - self.born

    @property
    def pinned(self) -> bool:
        return self.seconds <= PINNED

    @property
    def expired(self) -> bool:
        # Checked explicitly rather than falling out of the comparison: a
        # pinned mark has `seconds == 0`, and `age >= 0` is true the instant
        # it is born, so the obvious expression expires it immediately.
        return not self.pinned and self.age >= self.seconds

    @property
    def opacity(self) -> float:
        """1.0, then a fade at the end rather than a disappearance."""
        if self.pinned:
            return 1.0
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


def ellipse_outline(left, top, right, bottom, steps: int = CURVE_STEPS) -> list:
    """An ellipse as a closed polyline.

    Needed because a hand-drawn ring cannot be `pen.ellipse` - the wobble is
    applied along a path, and PIL's ellipse has no path to apply it to. The
    polyline is also what makes a circle and a rounded box the same code at
    stroke time.
    """
    centre = ((left + right) / 2.0, (top + bottom) / 2.0)
    radius = ((right - left) / 2.0, (bottom - top) / 2.0)
    points = []
    for step in range(steps + 1):
        angle = step / steps * math.tau
        points.append((centre[0] + radius[0] * math.cos(angle),
                       centre[1] + radius[1] * math.sin(angle)))
    return points


def rectangle_outline(left, top, right, bottom, radius: float = 0.0) -> list:
    """A rounded rectangle as a closed polyline, corners sampled as arcs."""
    radius = max(0.0, min(radius, (right - left) / 2.0, (bottom - top) / 2.0))
    if radius <= 0:
        return [(left, top), (right, top), (right, bottom), (left, bottom),
                (left, top)]

    per_corner = 8
    corners = (
        ((right - radius, top + radius), 270),
        ((right - radius, bottom - radius), 0),
        ((left + radius, bottom - radius), 90),
        ((left + radius, top + radius), 180),
    )
    points: list = []
    for (centre, start) in corners:
        for step in range(per_corner + 1):
            angle = math.radians(start + 90 * step / per_corner)
            points.append((centre[0] + radius * math.cos(angle),
                           centre[1] + radius * math.sin(angle)))
    points.append(points[0])
    return points


def wobbled(points: list, seed: int, amount: float,
            phase: float = 0.0) -> list:
    """The same path, pushed sideways by a slow sine. Deterministic per seed.

    Offsets are applied along the NORMAL to the path, not in x and y, so a
    wobbled circle stays a circle that someone drew badly rather than becoming
    an oval. The frequency is deliberately low - per-point random offsets give
    a fuzzy line, and what reads as a hand is a few long deviations.
    """
    if amount <= 0 or len(points) < 2:
        return list(points)

    # Two numbers out of the seed: where the wobble starts and how fast it
    # runs. Arithmetic rather than `random`, because this is called from the
    # render path and must not touch global state a test might be seeding.
    scramble = (seed * 2654435761) & 0xFFFFFFFF
    start = (scramble & 1023) / 1023.0 * math.tau + phase
    cycles = WOBBLE_CYCLES * (0.7 + ((scramble >> 10) & 255) / 255.0 * 0.6)

    total = len(points) - 1
    out = []
    for index, (x, y) in enumerate(points):
        ahead = points[min(index + 1, total)]
        behind = points[max(index - 1, 0)]
        dx, dy = ahead[0] - behind[0], ahead[1] - behind[1]
        length = math.hypot(dx, dy) or 1.0
        # Perpendicular to the direction of travel.
        nx, ny = -dy / length, dx / length
        push = math.sin(start + index / max(1, total) * cycles * math.tau)
        out.append((x + nx * push * amount, y + ny * push * amount))
    return out


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

    def label(self, at, text: str, leader=None, **kw) -> Mark:
        """Text on a plate. With `leader`, a line from the plate to a point.

        A label with nowhere to point is a caption; the leader is what makes
        it an annotation. Without it, "the razor tool" sat next to four icons
        and named none of them.
        """
        points = [tuple(at)] + ([tuple(leader)] if leader else [])
        return self._add(Mark("label", points, text=text, **kw))

    def rings(self, centre, radius: int = 34, **kw) -> Mark:
        """Concentric rings around a point - "this, here".

        A single circle has to be sized to the thing it encloses, which means
        knowing how big the thing is. Rings say "this point" whatever is under
        them, which is what grounding by sight can honestly claim when it is
        right to within thirty pixels.
        """
        return self._add(Mark("rings", [tuple(centre), (radius, radius)], **kw))

    def number(self, at, index: int, **kw) -> Mark:
        """A numbered badge. "Do these three, in this order."

        Also the Set-of-Mark primitive: a model handed numbered candidates
        and asked for an ID answers far better than one asked for a
        coordinate - measured elsewhere at 30% to 87% on the same model.
        """
        return self._add(Mark("number", [tuple(at)], text=str(index), **kw))

    def cursor(self, at, **kw) -> Mark:
        """The agent's OWN pointer - drawn, not the user's.

        A handed-over task that yanks the real pointer across the screen is
        taking the machine off somebody who is using it. Invariant 10 says
        they can always take the mouse back, and the honest reading of that
        is that unattended work should never have taken it: this is what a
        background agent points with instead.

        Drawn rather than glided, so it costs one mark and no time at all.
        """
        return self._add(Mark("cursor", [tuple(at)], **kw))

    def spotlight(self, regions, **kw) -> Mark:
        """Dim the screen except for these rectangles.

        `regions` is a list of (left, top, right, bottom). Dimming the
        irrelevant and lighting the relevant is the teaching cue with the most
        evidence behind it, and the one this project did not have.

        Drawn UNDERNEATH every other mark regardless of when it was added -
        see `render`. A spotlight added after an arrow would otherwise dim the
        arrow.
        """
        points: list = []
        for region in regions:
            left, top, right, bottom = region
            points.extend([(left, top), (right, bottom)])
        return self._add(Mark("spotlight", points, **kw))

    # --- housekeeping -----------------------------------------------------

    def _add(self, mark: Mark) -> Mark:
        self.marks.append(mark)
        return mark

    def clear(self, group: str | None = None) -> int:
        """Rub out everything, or one group. Returns how many went.

        A group rather than everything, because a walkthrough replaces the
        marks for step two without touching the ones explaining the window -
        and before groups the only choices were clearing the lot or waiting
        for a fade.
        """
        if group is None:
            gone = len(self.marks)
            self.marks.clear()
            return gone
        before = len(self.marks)
        self.marks = [mark for mark in self.marks if mark.group != group]
        return before - len(self.marks)

    def prune(self) -> bool:
        """Drop expired marks. True when something went."""
        before = len(self.marks)
        self.marks = [mark for mark in self.marks if not mark.expired]
        return len(self.marks) != before

    def groups(self) -> list[str]:
        """Which groups currently have marks, in the order they appeared."""
        seen: list[str] = []
        for mark in self.marks:
            if mark.group and mark.group not in seen:
                seen.append(mark.group)
        return seen

    @property
    def empty(self) -> bool:
        return not self.marks

    # --- what has to be repainted ----------------------------------------

    def extent(self, mark) -> tuple | None:
        """Where a mark paints, in SCREEN coordinates, or None if unknown.

        None means "repaint the whole screen for this one", which is the safe
        answer and is why this returns it rather than guessing. A box that is
        too small clips the mark, and a clipped ring looks like a rendering
        fault rather than like a wrong rectangle - so anything whose extent is
        not arithmetic says so.
        """
        kind, points = mark.kind, mark.points
        if not points:
            return None

        if kind == "spotlight":
            # It dims everything outside its holes, so its extent IS the
            # screen. Handled separately in `render` and never in a patch.
            return None

        if kind in ("circle", "rings"):
            # The outermost ring is drawn at the full radius; the inner ones
            # are smaller, so one radius bounds all three.
            (x, y), (radius, _) = points[0], points[1]
            corners = [(x - radius, y - radius), (x + radius, y + radius)]
        elif kind == "number":
            x, y = points[0]
            corners = [(x - BADGE_RADIUS, y - BADGE_RADIUS),
                       (x + BADGE_RADIUS, y + BADGE_RADIUS)]
        elif kind == "cursor":
            # Drawn down and to the RIGHT of its point, not centred - the
            # polygon runs to 1.12 of the size below and 0.7 across.
            x, y = points[0]
            corners = [(x - 1, y - 1),
                       (x + CURSOR_SIZE * 0.8, y + CURSOR_SIZE * 1.2)]
        elif kind == "arrow" or kind == "line":
            # The sampled curve stays inside the convex hull of the control
            # points, so the hull's bounding box bounds the curve.
            corners = bowed(points[0], points[1], mark.bow)
        elif kind == "label":
            measured = self._label_extent(mark)
            if measured is None:
                return None
            corners = measured
        else:
            # curve, path, box, ellipse, highlight - all bounded by their own
            # points, the first two because a Bezier stays inside its hull.
            corners = points

        margin = _mark_margin(mark)
        xs = [point[0] for point in corners]
        ys = [point[1] for point in corners]
        return (min(xs) - margin, min(ys) - margin,
                max(xs) + margin, max(ys) + margin)

    @staticmethod
    def _label_extent(mark) -> list | None:
        """A label's plate, measured with the real font.

        Text is the one mark whose size is not in its own coordinates, and
        guessing from the character count is how a long label gets its tail
        cut off. Measured at point size rather than supersampled and scaled
        back, since that is the same arithmetic with one less division.
        """
        try:
            font = _font(1, LABEL_POINTS)
            x, y = mark.points[0]
            box = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox(
                (x, y), mark.text, font=font)
        except Exception:  # noqa: BLE001 - an unmeasurable label repaints all
            return None
        corners = [(box[0], box[1]), (box[2], box[3])]
        if len(mark.points) > 1:
            corners.append(tuple(mark.points[1]))      # the leader's target
        return corners

    def patch(self, width: int, height: int,
              origin: tuple = (0, 0)) -> tuple | None:
        """The rectangle worth repainting, in OVERLAY coordinates.

        Spotlights are NOT in it. A spotlight dims everything outside itself,
        so no crop contains what it paints - but the ring sitting on top of it
        still only needs its own hundred pixels, so the two are separated and
        `render_patch` pastes one into the other.

        None means repaint every pixel - a mark whose extent cannot be known,
        or a box so large that cropping saves nothing. `(0, 0, 0, 0)` means
        there is no ink on this overlay at all, which happens when every mark
        is on another monitor.
        """
        boxes = []
        for mark in self.marks:
            if mark.opacity <= 0 or mark.kind == "spotlight":
                continue
            box = self.extent(mark)
            if box is None:
                return None
            boxes.append(box)
        if not boxes:
            return (0, 0, 0, 0)

        left = max(0, int(min(box[0] for box in boxes)) - origin[0])
        top = max(0, int(min(box[1] for box in boxes)) - origin[1])
        right = min(width, int(max(box[2] for box in boxes)) + 1 - origin[0])
        bottom = min(height, int(max(box[3] for box in boxes)) + 1 - origin[1])
        if right <= left or bottom <= top:
            return (0, 0, 0, 0)

        # Cropping a box that is most of the screen buys nothing and costs an
        # extra paste, so the full path stays for that case.
        if (right - left) * (bottom - top) > width * height * 0.8:
            return None
        return (left, top, right, bottom)

    # --- rendering --------------------------------------------------------

    def render(self, width: int, height: int,
               origin: tuple = (0, 0)) -> Image.Image:
        """An RGBA image of every live mark, in screen coordinates.

        `origin` is the overlay's top-left on the virtual desktop, so marks
        given in screen coordinates land in the right place on a second
        monitor - whose coordinates are negative when it sits to the left.
        """
        image, (left, top) = self.render_patch(width, height, origin)
        if image.size == (width, height) and (left, top) == (0, 0):
            return image
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(image, (left, top))
        return canvas

    def render_patch(self, width: int, height: int,
                     origin: tuple = (0, 0)) -> tuple:
        """(image, (left, top)) - only the part of the overlay that has ink.

        The caller composites it, which is the point: a 2560x1600 board takes
        266ms to downscale from its supersampled canvas and the ring on it
        occupies 150 pixels. `Board.draw` writes the patch into a buffer it
        keeps, so nothing larger than the ink is ever touched.
        """
        box = self.patch(width, height, origin)
        # Spotlights last in the code and FIRST in the picture. A shadow is a
        # layer with holes in it rather than a shape, so it cannot be drawn
        # with the pen, and drawn in sequence it would dim every mark added
        # before it.
        shadows = [mark for mark in self.marks
                   if mark.kind == "spotlight" and mark.opacity > 0]

        if box == (0, 0, 0, 0) and not shadows:
            # Every mark is off this overlay - they are all on another
            # monitor. One transparent pixel rather than a transparent
            # screen: the picture is the same either way, the caller
            # composites it, and there is no reason to allocate and
            # premultiply four million pixels of nothing.
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0)), (0, 0)
        if box == (0, 0, 0, 0):
            return self._shadow(shadows, (width, height), origin), (0, 0)
        left, top, right, bottom = box or (0, 0, width, height)

        scale = SUPERSAMPLE
        size = (right - left, bottom - top)
        canvas = Image.new("RGBA", (size[0] * scale, size[1] * scale),
                           (0, 0, 0, 0))

        def place(point):
            return ((point[0] - origin[0] - left) * scale,
                    (point[1] - origin[1] - top) * scale)

        pen = ImageDraw.Draw(canvas, "RGBA")
        for mark in self.marks:
            if mark.kind == "spotlight":
                continue
            alpha = int(255 * mark.opacity)
            if alpha <= 0:
                continue
            colour = (*mark.colour, alpha)
            thickness = max(1, mark.width * scale)
            self._draw(pen, mark, colour, thickness, place, scale)

        marks = canvas.resize(size, Image.LANCZOS)
        if not shadows:
            return marks, (left, top)

        # The shadow is the one thing here that genuinely is screen-sized, and
        # it is built at OUTPUT size rather than supersampled: it is a soft
        # dark region whose only curve is a corner radius, and building it at
        # 2x and shrinking it cost 37ms to make something nobody can tell
        # apart.
        shadow = self._shadow(shadows, (width, height), origin)
        if box is None:
            return Image.alpha_composite(shadow, marks), (0, 0)
        # The marks are a crop, so only that part of the shadow is composited
        # and pasted back. Compositing the full layer instead costs a second
        # pass over every pixel to change a hundred of them.
        shadow.paste(Image.alpha_composite(shadow.crop(box), marks), box)
        return shadow, (0, 0)

    @staticmethod
    def _shadow(marks, size, origin) -> Image.Image:
        """A dark layer with the lit regions punched out of it.

        Built as a MASK and pasted, not drawn: PIL's RGBA draw mode blends,
        so drawing a transparent rectangle over the shadow does nothing at
        all. The alpha channel is painted instead, which is the only way to
        make a hole.
        """
        strongest = max(mark.opacity for mark in marks)
        mask = Image.new("L", size, int(SHADOW_ALPHA * strongest))
        cutter = ImageDraw.Draw(mask)
        for mark in marks:
            corners = [(point[0] - origin[0], point[1] - origin[1])
                       for point in mark.points]
            for (left, top), (right, bottom) in zip(corners[::2], corners[1::2]):
                cutter.rounded_rectangle(
                    [min(left, right), min(top, bottom),
                     max(left, right), max(top, bottom)],
                    radius=10, fill=0)
        shadow = Image.new("RGBA", size, (*SHADOW, 255))
        shadow.putalpha(mask)
        return shadow

    def _stroke(self, pen, points, colour, thickness, mark, scale) -> list:
        """Draw a path. Twice and wobbling, when the look is hand-drawn.

        Returns the points actually drawn for the LAST pass, so an arrowhead
        can align with the line it is on rather than with the clean geometry
        the line was computed from.
        """
        if len(points) < 2:
            return list(points)
        if not HAND_DRAWN:
            pen.line(points, fill=colour, width=thickness, joint="curve")
            return list(points)

        # Clamped to the SIZE of the thing being drawn. A fixed three pixels
        # is a hand on a circle sixty across and a destroyed shape on one
        # seven across - the innermost target ring came out as a scribble,
        # because the wobble was most of its radius.
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        span = min(max(xs) - min(xs), max(ys) - min(ys))
        amount = min(WOBBLE_PIXELS * scale, span * 0.07)
        if amount <= 0.5:
            pen.line(points, fill=colour, width=thickness, joint="curve")
            return list(points)

        # Each pass is thinner than the nominal width - two full-width strokes
        # side by side read as a double line rather than as one pen - and the
        # second is shallower than the first, so the two cross instead of
        # running parallel. Parallel is a drafting convention; crossing is a
        # pen going over its own line.
        pass_width = max(1, int(thickness * 0.72))
        drawn = points
        for index in range(SKETCH_PASSES):
            drawn = wobbled(points, mark.seed + index * 977,
                            amount * (1.0 - index * 0.4), phase=index * 1.9)
            pen.line(drawn, fill=colour, width=pass_width, joint="curve")
        return drawn

    def _draw(self, pen, mark, colour, thickness, place, scale) -> None:
        kind = mark.kind

        if kind in ("line", "arrow"):
            control = bowed(mark.points[0], mark.points[1], mark.bow)
            sampled = [place(p) for p in bezier(control)]
            drawn = self._stroke(pen, sampled, colour, thickness, mark, scale)
            if kind == "arrow":
                self._head(pen, drawn, colour, thickness)
            return

        if kind in ("curve", "path"):
            points = [place(p) for p in (
                bezier(mark.points) if kind == "curve" else mark.points)]
            self._stroke(pen, points, colour, thickness, mark, scale)
            return

        if kind == "box":
            (left, top), (right, bottom) = (place(p) for p in mark.points)
            self._stroke(pen, rectangle_outline(left, top, right, bottom,
                                                6 * scale),
                         colour, thickness, mark, scale)
            return

        if kind == "circle":
            centre = place(mark.points[0])
            radius = mark.points[1][0] * scale
            self._stroke(pen, ellipse_outline(centre[0] - radius,
                                              centre[1] - radius,
                                              centre[0] + radius,
                                              centre[1] + radius),
                         colour, thickness, mark, scale)
            return

        if kind == "rings":
            centre = place(mark.points[0])
            radius = mark.points[1][0] * scale
            for ring in range(TARGET_RINGS):
                size = radius * (1.0 - ring * RING_STEP)
                # Below this a ring is a dot, and three dots inside each
                # other is not a target.
                if size <= 7 * scale:
                    break
                # Each ring fainter than the one outside it, so the eye runs
                # inward to the point rather than stopping at the outline.
                faded = (*mark.colour,
                         max(30, int(colour[3] * (1.0 - ring * 0.22))))
                self._stroke(pen, ellipse_outline(centre[0] - size,
                                                  centre[1] - size,
                                                  centre[0] + size,
                                                  centre[1] + size),
                             faded, max(1, thickness - ring), mark, scale)
            return

        if kind == "ellipse":
            (left, top), (right, bottom) = (place(p) for p in mark.points)
            self._stroke(pen, ellipse_outline(left, top, right, bottom),
                         colour, thickness, mark, scale)
            return

        if kind == "highlight":
            (left, top), (right, bottom) = (place(p) for p in mark.points)
            # The wash is a real rectangle: a wobbly fill looks like a
            # mistake, while a wobbly outline looks drawn. Different jobs.
            wash = (*mark.colour, int(colour[3] * 0.22))
            pen.rounded_rectangle([left, top, right, bottom],
                                  radius=8 * scale, fill=wash)
            self._stroke(pen, rectangle_outline(left, top, right, bottom,
                                                8 * scale),
                         colour, max(1, thickness // 2), mark, scale)
            return

        if kind == "number":
            centre = place(mark.points[0])
            radius = BADGE_RADIUS * scale
            # A filled badge, not an outline. A number has to be legible on
            # whatever is underneath it, and an outlined digit over a
            # screenshot is the least readable thing available.
            pen.ellipse([centre[0] - radius, centre[1] - radius,
                         centre[0] + radius, centre[1] + radius],
                        fill=(*mark.colour, colour[3]),
                        outline=(255, 255, 255, colour[3]),
                        width=max(1, scale))
            font = _font(scale, BADGE_POINTS)
            pen.text(centre, mark.text, fill=(255, 255, 255, colour[3]),
                     font=font, anchor="mm")
            return

        if kind == "cursor":
            x, y = place(mark.points[0])
            size = CURSOR_SIZE * scale
            # The classic arrow, as a polygon, with a pale outline so it is
            # visible on a dark panel and on a white page both. Filled
            # rather than sketched: this one is a pointer and has to read as
            # precise, where every other mark here reads as a suggestion.
            arrow = [(x, y),
                     (x, y + size),
                     (x + size * 0.26, y + size * 0.74),
                     (x + size * 0.44, y + size * 1.12),
                     (x + size * 0.60, y + size * 1.04),
                     (x + size * 0.42, y + size * 0.68),
                     (x + size * 0.70, y + size * 0.66)]
            pen.polygon(arrow, fill=(*mark.colour, colour[3]),
                        outline=(255, 255, 255, colour[3]),
                        width=max(1, scale))
            return

        if kind == "label":
            x, y = place(mark.points[0])
            font = _font(scale)
            # A plate behind it, because a label is unreadable on exactly the
            # busy screenshot somebody most needs it explained on.
            box = pen.textbbox((x, y), mark.text, font=font)
            pad = 5 * scale
            if len(mark.points) > 1:
                # The leader runs from the edge of the plate to the thing,
                # drawn FIRST so the plate covers where it starts.
                target = place(mark.points[1])
                anchor = (box[0] + (box[2] - box[0]) / 2,
                          box[1] + (box[3] - box[1]) / 2)
                self._stroke(pen, [anchor, target], colour,
                             max(1, thickness // 2), mark, scale)
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
        # The window's whole pixel buffer, kept between frames, and the part
        # of it that currently holds ink. UpdateLayeredWindow wants the lot
        # every time, so there is no such thing as a partial blit - but there
        # is no need to BUILD the lot every time either. Premultiplying a
        # 2560x1600 layer is eight passes over four million pixels, 51ms, to
        # produce a buffer that is transparent everywhere except a ring.
        #
        # So the buffer is written once and then only where the ink is: the
        # previous ink is zeroed and the new patch written over it, both of
        # which touch the mark's own rectangle and nothing else.
        self._buffer: bytearray | None = None
        self._inked: tuple | None = None

    @property
    def overlay(self):
        """The window itself, for the one caller that has to change it.

        Marking mode turns click-through off while the user draws, which is
        a property of the window rather than of the marks - so it cannot go
        through `Sketch`, and reaching into `_overlay` from another module
        would be worse than saying it is reachable.
        """
        return self._overlay

    def draw(self) -> None:
        """Push the current marks. Cheap when nothing is live.

        Safe to call every frame: it returns immediately when the board is
        empty, and throttles when it is not.
        """
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

        image, at = self.sketch.render_patch(
            self.monitor.width, self.monitor.height, self.origin)
        self._overlay.draw(self._buffered(image, at))
        if not self._showing:
            self._overlay.show()
            self._showing = True
        self._blank = False

    def _buffered(self, image, at: tuple) -> bytearray:
        """The whole window's pixels, with only the new ink rewritten.

        `image` is whatever `render_patch` produced - the crop around the
        marks, or the full layer when a spotlight means there is no crop.
        """
        from ..cat import rgba_to_premultiplied_bgra

        width, height = self.monitor.width, self.monitor.height
        stride = width * 4
        patch = rgba_to_premultiplied_bgra(image)

        if image.size == (width, height):
            # Nothing to splice: this IS the window. Cheaper than writing it
            # into a buffer row by row, and it leaves the whole thing inked.
            self._buffer = patch
            self._inked = (0, 0, width, height)
            return patch

        if self._buffer is None or len(self._buffer) != stride * height:
            self._buffer = bytearray(stride * height)
            self._inked = None

        buffer = self._buffer
        if self._inked is not None:
            left, top, right, bottom = self._inked
            blank = bytes((right - left) * 4)
            for row in range(top, bottom):
                start = row * stride + left * 4
                buffer[start:start + len(blank)] = blank

        left, top = at
        patch_width, patch_height = image.size
        row_bytes = patch_width * 4
        for row in range(patch_height):
            start = (top + row) * stride + left * 4
            buffer[start:start + row_bytes] = patch[
                row * row_bytes:(row + 1) * row_bytes]
        self._inked = (left, top, left + patch_width, top + patch_height)
        return buffer

    def clear(self, group: str | None = None) -> int:
        """Rub out everything, or one group. Returns how many marks went."""
        gone = self.sketch.clear(group)
        self.draw()
        return gone

    def close(self) -> None:
        try:
            self._overlay.close()
        except Exception:  # noqa: BLE001 - teardown must not raise
            pass
