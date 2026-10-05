"""Letting the user draw on their own screen, and holding what they drew.

The state machine is `meow.desktop.marking` and knows nothing about Windows.
This is the part with the mouse in it: one frame at a time, on the render
thread, because it owns the marks overlay and a Win32 window belongs to the
thread that made it.

**The overlay stops being click-through while this runs, and that is the
whole trick.** Every overlay here passes clicks to whatever is underneath,
which is right for all of them except this one: a click-through layer would
let the drag land in Photoshop, so somebody circling a thing to ask about it
would be drawing on their own artwork. `set_click_through(False)` swallows
the drag, and `WS_EX_NOACTIVATE` means it still never takes focus away from
whatever they were typing in.

**0x8000 is the bit to read, not 0x0001.** `GetAsyncKeyState`'s low bit is
"pressed at any point since somebody last asked", which accumulates while
nobody is asking - the agent dock opened a window nobody asked for because
of it. A drag needs "is the button down RIGHT NOW", which is the high bit,
and it has no history to go stale.
"""

from __future__ import annotations

import ctypes

from ..desktop.annotate import ACCENT, PINNED
from ..desktop.marking import Marking, Region

user32 = ctypes.windll.user32

VK_LBUTTON = 0x01
VK_ESCAPE = 0x1B
HELD = 0x8000

# The marks drawn while somebody is circling. Their own group, so finishing a
# stroke can replace the last one without touching anything the cat drew.
GROUP = "user-mark"

# A full-screen wash while marking, so it is obvious the screen is in a mode
# and obvious which part is about to be captured. Not a spotlight - there is
# nothing to light yet.
PROMPT = "draw round what you mean"


def button_is_down(key: int = VK_LBUTTON) -> bool:
    """Is the button down right now? Not "was it pressed at some point"."""
    return bool(user32.GetAsyncKeyState(key) & HELD)


class Pencil:
    """Marking mode: off, or collecting one stroke.

    `tick` is called every frame and does nothing at all when off, which is
    almost always. When on it is two Win32 calls and, at most, one point
    appended to a list.
    """

    def __init__(self, board, say=None) -> None:
        # The Board, not a factory: this runs on the thread that made the
        # overlay, and reaching for one lazily from anywhere else is the
        # WinError 1400 that took a feature down after it had visibly worked.
        self.board = board
        self.say = say
        self.marking: Marking | None = None
        self.region: Region | None = None

    @property
    def active(self) -> bool:
        return self.marking is not None

    def begin(self) -> bool:
        """Enter marking mode. False when there is nowhere to draw."""
        if self.board is None:
            return False
        if self.marking is not None:
            # Tapped twice. The second tap means they changed their mind,
            # which is the same thing the escape key means.
            self.cancel()
            return False

        self.marking = Marking()
        self.board.sketch.clear(group=GROUP)
        try:
            self.board.overlay.set_click_through(False)
        except Exception:  # noqa: BLE001 - drawing is never worth a crash
            self.marking = None
            return False

        self.board.sketch.label((40, 40), PROMPT, colour=ACCENT,
                                seconds=PINNED, group=GROUP)
        self.board.draw()
        if self.say:
            self.say("draw round what you mean.")
        return True

    def tick(self) -> None:
        """One frame of marking. Returns immediately when off."""
        marking = self.marking
        if marking is None:
            return

        if user32.GetAsyncKeyState(VK_ESCAPE) & HELD:
            self.cancel()
            return

        from ..platform.monitors import get_cursor_position

        changed = marking.update(get_cursor_position(),
                                 button_is_down())
        if changed:
            self._redraw(marking)

        if marking.finished:
            self._finish(marking)

    def cancel(self) -> None:
        """Leave marking mode with nothing captured."""
        if self.marking is not None:
            self.marking.cancel()
        self.marking = None
        self._release()
        if self.board is not None:
            self.board.sketch.clear(group=GROUP)
            self.board.draw()

    def take(self) -> Region | None:
        """The region, consumed. None once it has been used or gone stale.

        Consumed rather than read, because a region that survives the turn it
        was drawn for silently narrows the next question - everything works,
        the answers are about the wrong part of the screen, and nothing in
        the reply says so.
        """
        region = self.region
        self.region = None
        if region is None or region.stale:
            return None
        return region

    def forget(self) -> None:
        """Drop the region and rub out the stroke."""
        self.region = None
        if self.board is not None:
            self.board.sketch.clear(group=GROUP)
            self.board.draw()

    # --- the drawing -----------------------------------------------------

    def _redraw(self, marking: Marking) -> None:
        if self.board is None:
            return
        self.board.sketch.clear(group=GROUP)
        self.board.sketch.label((40, 40), PROMPT, colour=ACCENT,
                                seconds=PINNED, group=GROUP)
        if len(marking.points) >= 2:
            # Freehand, so the points are followed exactly. A curve through
            # them would smooth out the shape somebody deliberately drew.
            self.board.sketch.path(marking.points, colour=ACCENT,
                                   seconds=PINNED, group=GROUP)
        self.board.draw()

    def _finish(self, marking: Marking) -> None:
        self.marking = None
        self.region = marking.region
        self._release()
        if self.board is None:
            return

        self.board.sketch.clear(group=GROUP)
        if self.region is not None:
            # Kept on screen so they can see what was captured, and pinned so
            # it is still there while they finish the sentence about it.
            self.board.sketch.box(self.region.left, self.region.top,
                                  self.region.right, self.region.bottom,
                                  colour=ACCENT, seconds=PINNED, group=GROUP)
        self.board.draw()

    def _release(self) -> None:
        """Give clicks back to whatever is underneath."""
        if self.board is None:
            return
        try:
            self.board.overlay.set_click_through(True)
        except Exception:  # noqa: BLE001 - and never leave the screen stuck
            pass
