"""Showing somebody where a thing is, by drawing on their screen.

`point_at_control` moves the pointer to a control the accessibility tree
knows. That covers Notepad and Settings and fails completely on the things
people actually want taught: a chess board, an anatomy diagram, a molecule, a
video editor's timeline. None of those have controls, and a cursor sitting
somewhere says "there" without saying what shape "there" is.

So these tools ground through `HybridGrounding` - the tree first, because it
is free and exact, and the computer-use model when the tree is blind - and
then DRAW. Measured on hand-labelled targets: chess pieces and board squares
76%, dense professional toolbars 12-17%. That split is why the marks are
soft-edged suggestions rather than precise claims, and why none of this ever
clicks.

**Nothing here changes anything.** Every tool draws and returns; the marks
expire on their own. That is what makes a 76% grounding rate acceptable -
being wrong means an arrow in the wrong place for a few seconds, which the
user corrects in a sentence. At the same accuracy a click would be
indefensible.
"""

from __future__ import annotations

from langchain_core.tools import tool

from ..desktop.annotate import ACCENT, INK
from .record import ToolRun
from ..desktop.actions import Outcome


def build(harness) -> list:
    """The tools in this module, bound to one harness."""

    @tool
    def show_on_screen(description: str, shape: str = "circle") -> str:
        """Draw a mark on screen around something, so the user can SEE it.

        Use whenever explaining where a thing is - "the white queen", "the
        razor tool", "the left kidney on this diagram". Works on pictures and
        canvases where there are no controls at all.

        shape: circle, box, arrow or highlight.
        """
        target = harness.locate_anything(description)
        if target is None:
            harness.runs.append(ToolRun("show_on_screen", description,
                                        Outcome(False, "could not find it")))
            return (f"I could not find {description!r} on this screen. Say "
                    f"roughly where it is and I will look again - do not "
                    f"guess at a place for them.")

        board = harness.board()
        if board is None:
            return f"Found {description}, but I cannot draw on this screen."

        left, top, right, bottom = (target.left, target.top,
                                    target.right, target.bottom)
        pad = 18
        if shape == "box":
            board.sketch.box(left - pad, top - pad, right + pad, bottom + pad)
        elif shape == "highlight":
            board.sketch.highlight(left - pad, top - pad,
                                   right + pad, bottom + pad)
        elif shape == "arrow":
            centre = target.centre
            # Comes in from below-left, so the head does not sit on top of the
            # thing it is indicating.
            board.sketch.arrow((centre[0] - 180, centre[1] + 140), centre,
                               bow=0.25)
        else:
            centre = target.centre
            radius = max(28, (right - left) // 2 + pad)
            board.sketch.circle(centre, radius)
        board.draw()

        harness.runs.append(ToolRun("show_on_screen", description,
                                    Outcome(True, f"drew a {shape}"), target))
        exact = "exactly" if target.is_exact else "roughly"
        return (f"Drawn a {shape} {exact} around {description}. Tell them it "
                f"is marked on their screen. Say nothing about coordinates.")

    @tool
    def draw_a_move(from_description: str, to_description: str) -> str:
        """Draw a curved arrow from one thing on screen to another.

        For showing a MOVE or a relationship: a chess piece to the square it
        should go to, one part of a diagram to another, a clip to where it
        belongs on a timeline.
        """
        start = harness.locate_anything(from_description)
        end = harness.locate_anything(to_description)
        missing = [name for name, found in
                   ((from_description, start), (to_description, end))
                   if found is None]
        if missing:
            harness.runs.append(ToolRun("draw_a_move", from_description,
                                        Outcome(False, "could not find both")))
            return (f"I could not find {' or '.join(repr(m) for m in missing)} "
                    f"on this screen, so I have drawn nothing.")

        board = harness.board()
        if board is None:
            return "I cannot draw on this screen."

        # Bowed, because a straight line between two squares on a board reads
        # as a boundary rather than a movement.
        board.sketch.circle(start.centre, 34, colour=ACCENT, seconds=8.0)
        board.sketch.arrow(start.centre, end.centre, bow=0.28,
                           colour=INK, seconds=8.0)
        board.draw()
        harness.runs.append(ToolRun(
            "draw_a_move", f"{from_description} -> {to_description}",
            Outcome(True, "drew the move"), end))
        return (f"Drawn an arrow from {from_description} to "
                f"{to_description}. Say what the move is, not where it is.")

    @tool
    def clear_the_screen() -> str:
        """Rub out every mark drawn so far."""
        board = harness.board()
        if board is not None:
            board.clear()
        harness.runs.append(ToolRun("clear_the_screen", "",
                                    Outcome(True, "cleared")))
        return "Cleared the marks."

    return [
        show_on_screen,
        draw_a_move,
        clear_the_screen,
    ]
