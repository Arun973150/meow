"""The user pointing at their own screen - the other direction of spatial
context.

Everything else in this project runs one way: the cat looks at the screen and
marks what it found. This is the way back. Somebody circles the thing they
mean and then asks about it, and the circle does more work than any sentence
they could have said instead.

**It is a grounding signal, not a gesture.** Grounding by sight was measured
at 23 of 44 hand-labelled targets, and the failures cluster exactly where the
screen is busiest - Illustrator 1 of 8, Premiere 1 of 6. The search space is
the whole of a professional interface, and a circle collapses it to one
region. Nothing else available here changes the problem that much: it is the
difference between "find the razor tool somewhere in Resolve" and "find it in
this box", and the second question is one a small model can answer.

**Nothing here touches Windows.** It takes a cursor position and whether the
button is down, and returns what to draw and when the stroke is finished, so
the whole thing is testable with a list of coordinates. The polling, the
overlay and the hotkey are in `meow/app/`.

**A region EXPIRES.** A stale one silently narrowing a later question is the
worst kind of bug: everything works, the answers are quietly about the wrong
part of the screen, and nothing in the reply says so. It is used once and
cleared, and it times out even if it is never used.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# How far the pointer has to travel before another point is kept. A mouse
# reports far more detail than a freehand line needs, and a path with eight
# hundred points in it costs a redraw every frame while it is being drawn.
MINIMUM_TRAVEL = 6

# A drag shorter than this is a CLICK, and a click is a point rather than a
# region - "this one", with no size claimed. Measured off a real trackpad: a
# deliberate tap moves two or three pixels, so this has headroom.
TAP_PIXELS = 14

# A tap means "here", and the region round it has to be big enough to contain
# whatever "here" was without being so big it stops narrowing anything.
TAP_RADIUS = 60

# Breathing room round a drawn stroke. People circle generously but not
# precisely, and a box cropped to the ink alone can cut the edge off the thing
# they drew round.
PADDING = 12

# After this a region is thrown away unmarked. Somebody who circled something,
# wandered off and came back twenty minutes later is asking a new question,
# and answering it about the old region would be wrong in a way nothing in the
# reply would reveal.
STALE_SECONDS = 120.0


@dataclass(frozen=True)
class Region:
    """A rectangle on the virtual desktop that the user drew round."""

    left: int
    top: int
    right: int
    bottom: int
    # True when it came from a tap rather than a stroke. A tap says "this
    # point" and claims no extent, which matters to anything that would
    # otherwise report the box as the size of the thing.
    tapped: bool = False
    drawn: float = field(default_factory=time.monotonic)

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def centre(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    @property
    def stale(self) -> bool:
        return time.monotonic() - self.drawn > STALE_SECONDS

    def contains(self, point) -> bool:
        x, y = point
        return self.left <= x <= self.right and self.top <= y <= self.bottom

    def describe(self) -> str:
        """For the model. Says what it IS, never where it is on screen."""
        if self.tapped:
            return "the point the user pointed at"
        return "the area the user drew round"


@dataclass
class Marking:
    """One session of the user drawing on their screen.

    Driven a frame at a time by `update`, which is given the cursor position
    and whether the button is down. It returns the live stroke, so the caller
    can draw it, and sets `finished` once the button comes back up.
    """

    points: list = field(default_factory=list)
    started: bool = False
    finished: bool = False
    region: Region | None = None
    _was_down: bool = field(default=False, repr=False)

    def update(self, cursor, button_down: bool) -> bool:
        """One frame. True when the picture changed and wants redrawing."""
        if self.finished:
            return False

        point = (int(cursor[0]), int(cursor[1]))

        if button_down and not self._was_down:
            # A fresh press. Anything drawn before it was a previous attempt
            # the user abandoned by lifting and starting again.
            self.points = [point]
            self.started = True
            self._was_down = True
            return True

        if button_down:
            self._was_down = True
            if not self.points:
                self.points = [point]
                return True
            last = self.points[-1]
            if (abs(point[0] - last[0]) + abs(point[1] - last[1])
                    < MINIMUM_TRAVEL):
                return False
            self.points.append(point)
            return True

        if self._was_down:
            # Lifted. Whatever is on screen is what they meant.
            self._was_down = False
            self.finished = True
            self.region = self._region()
            return True

        return False

    def cancel(self) -> None:
        """Ended without a region - they changed their mind, or ran out."""
        self.finished = True
        self.region = None

    def _region(self) -> Region | None:
        if not self.points:
            return None

        xs = [point[0] for point in self.points]
        ys = [point[1] for point in self.points]
        left, right = min(xs), max(xs)
        top, bottom = min(ys), max(ys)

        if (right - left) <= TAP_PIXELS and (bottom - top) <= TAP_PIXELS:
            # A tap, not a circle. Claiming a fourteen-pixel region around it
            # would narrow the search to less than one icon.
            x, y = self.points[-1]
            return Region(x - TAP_RADIUS, y - TAP_RADIUS,
                          x + TAP_RADIUS, y + TAP_RADIUS, tapped=True)

        return Region(left - PADDING, top - PADDING,
                      right + PADDING, bottom + PADDING)
