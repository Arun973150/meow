"""Watching for the click that completes a lesson step.

A `named` step advances when UIA sees the next control; a `doing` step
advances when the person says so. Neither works for a click inside Blender
or Photoshop: the tree is blind there, and nobody wants to say "done" out
loud after every click. So the step draws a ring and this watches the mouse.

**On the render thread, one frame at a time, like `Pencil`.** Two Win32
calls - `GetAsyncKeyState` and `GetCursorPos` - are microseconds, so there
is nothing here worth a thread, no lock, and no second answer to "where is
the pointer". That is the opposite of the walkthrough watcher, which is on
its own thread because a UIA digest is 268ms and would stutter the cat.

**0x8000, not 0x0001**, and the first read after arming is DISCARDED. The
low bit is "pressed at some point since anyone last asked" and accumulates
while nobody is asking - that is what once opened a chat window nobody asked
for, and here it would complete a step with a click from before the step
existed. `sketching.button_is_down` reads the right bit; the discard is this
module's job, because only it knows when a step began.

**It completes on RELEASE, not on press.** A press inside the ring that
drags out of it is somebody moving a slider or dragging a file, not clicking
the thing - and completing on the press would advance the lesson in the
middle of their drag.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

# How far outside an EXACT rectangle a click still counts. A UIA rectangle is
# the control's real extent, so this only forgives the edge - somebody aiming
# at a 20px icon and landing one pixel outside it meant to hit it.
EXACT_PADDING = 6

# For a SIGHTED target there is no rectangle, only a point - and the measured
# accuracy of that point is 52% exact, 61% within 50px. So the ring has a
# radius, the acceptance has the same radius, and both are honest about what
# was actually found. A click 40px from a guess that is itself 30px out is
# the user hitting the thing we meant.
SIGHTED_RADIUS = 46

# Nobody follows a step instantly, and nobody takes ten minutes over one. At
# four minutes the lesson says so rather than waiting silently forever -
# the same number as a task's unanswered question, and for the same reason:
# silence is not an answer, and it is also not invisible.
PATIENCE_SECONDS = 240.0

# How long the pointer has to sit on a hover target before the step counts as
# done. Long enough that crossing the thing on the way somewhere else is not
# an answer, short enough that somebody deliberately resting on it does not
# wonder whether it worked. A tooltip appears at about half a second, so this
# is roughly "they waited for the tooltip".
REST_SECONDS = 0.9

# How far the pointer may wander while resting. A hand on a trackpad is never
# still, and this project already measured the pointer drifting a median of
# 0px with spikes to 57px while nothing touched it - so a strict "has not
# moved" test would never fire.
REST_WANDER = 12


class Clicked(Enum):
    """How the wait for a click ended."""

    WAITING = "waiting"          # nothing yet, say nothing
    ON_TARGET = "on target"      # they clicked the thing: next step
    RESTED = "rested"            # they held the pointer on it: a hover step
    OFF_TARGET = "off target"    # they clicked somewhere else
    STEPPED_AWAY = "stepped away"  # another window came to the front
    GAVE_UP = "gave up"          # four minutes, no click


@dataclass
class Where:
    """The region a click has to land in, and how sure we are of it.

    Built from a `Target`, so the acceptance comes from how the thing was
    FOUND. An exact rectangle out of the tree is the control itself; a
    sighted point is a guess with a measured error, and pretending otherwise
    would reject clicks that were aimed correctly at a ring drawn slightly
    wrong.
    """

    left: int
    top: int
    right: int
    bottom: int
    exact: bool

    @classmethod
    def around(cls, target) -> "Where":
        if getattr(target, "is_exact", False):
            return cls(target.left - EXACT_PADDING, target.top - EXACT_PADDING,
                       target.right + EXACT_PADDING,
                       target.bottom + EXACT_PADDING, True)
        centre_x, centre_y = target.centre
        return cls(centre_x - SIGHTED_RADIUS, centre_y - SIGHTED_RADIUS,
                   centre_x + SIGHTED_RADIUS, centre_y + SIGHTED_RADIUS, False)

    def contains(self, point) -> bool:
        x, y = point
        return self.left <= x <= self.right and self.top <= y <= self.bottom


class Watch:
    """One step's wait for a click. `tick` every frame; `arm` to start.

    Does nothing at all when not armed, which is almost always - the same
    bargain as `Pencil.tick`.
    """

    def __init__(self, cursor=None, button=None, foreground=None) -> None:
        # Injected so this is testable without Windows. The real ones are
        # three calls; a fake one is a list of positions.
        self._cursor = cursor
        self._button = button
        self._foreground = foreground
        self.where: Where | None = None
        self._armed_at = 0.0
        self._window = ""
        # The first poll after arming is thrown away. See the module note:
        # the was-pressed bit is not read here, but a button that is ALREADY
        # down when a step begins is somebody mid-drag, and treating the
        # release of that drag as their answer to a step they have not read
        # is the same bug wearing different clothes.
        self._settled = False
        self._was_down = False
        self._resting = False
        self._rested_from = 0.0
        self._rested_at = (0, 0)

    # --- the three Win32 reads, late-bound so tests need no Windows ------

    def _pointer(self):
        if self._cursor is not None:
            return self._cursor()
        from ..platform.monitors import get_cursor_position

        return get_cursor_position()

    def _down(self) -> bool:
        if self._button is not None:
            return self._button()
        from .sketching import button_is_down

        return button_is_down()

    def _front(self) -> str:
        if self._foreground is not None:
            return self._foreground()
        try:
            import ctypes

            handle = ctypes.windll.user32.GetForegroundWindow()
            return str(handle)
        except Exception:  # noqa: BLE001 - not knowing is not stepping away
            return self._window

    # --- the state machine ------------------------------------------------

    @property
    def armed(self) -> bool:
        return self.where is not None

    def arm(self, target, resting: bool = False) -> None:
        """Start waiting for a click on this target, or a rest on it.

        `resting` is for a step whose instruction is to HOVER - a menu that
        opens on hover, a tooltip, a timeline preview. There is no click
        coming, so waiting for one is waiting forever.
        """
        self.where = Where.around(target)
        self._armed_at = time.time()
        self._window = self._front()
        self._settled = False
        self._was_down = False
        self._resting = resting
        self._rested_from = 0.0
        self._rested_at = (0, 0)

    def disarm(self) -> None:
        self.where = None
        self._settled = False
        self._was_down = False
        self._resting = False
        self._rested_from = 0.0

    def tick(self) -> Clicked:
        """One frame. WAITING almost always."""
        if self.where is None:
            return Clicked.WAITING

        if self._front() != self._window:
            # STEP-AWAY, and checked FIRST. Somebody who has gone to another
            # window has left whatever the mouse is doing: the ring is over a
            # window the lesson is not about, so there is nothing to settle
            # and nothing to wait for. Said once, never nagged about.
            return Clicked.STEPPED_AWAY

        down = self._down()
        if not self._settled:
            # The discarded first read. A button already down when the step
            # began belongs to whatever they were doing before it.
            self._settled = not down
            self._was_down = down
            return Clicked.WAITING

        if down:
            self._was_down = True
            return Clicked.WAITING

        if self._was_down:
            # Released. Completing on the RELEASE is what tells a click from
            # a drag that started on the thing and went somewhere else.
            self._was_down = False
            where = self._pointer()
            return (Clicked.ON_TARGET if self.where.contains(where)
                    else Clicked.OFF_TARGET)

        if self._resting and self._rest_is_done():
            return Clicked.RESTED

        if time.time() - self._armed_at > PATIENCE_SECONDS:
            return Clicked.GAVE_UP
        return Clicked.WAITING

    def _rest_is_done(self) -> bool:
        """Has the pointer sat on the target long enough to mean it?

        Only ever consulted for a hover step. The clock starts when the
        pointer arrives and is thrown away the moment it leaves or wanders,
        so crossing the target on the way somewhere else never completes a
        step.
        """
        where = self._pointer()
        if self.where is None or not self.where.contains(where):
            self._rested_from = 0.0
            return False
        if self._rested_from:
            moved = (abs(where[0] - self._rested_at[0])
                     + abs(where[1] - self._rested_at[1]))
            if moved > REST_WANDER:
                # Moving WITHIN the target is still not resting on it - they
                # are on their way across it.
                self._rested_from = time.time()
                self._rested_at = where
                return False
            return time.time() - self._rested_from >= REST_SECONDS
        self._rested_from = time.time()
        self._rested_at = where
        return False
