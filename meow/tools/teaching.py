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

from ..desktop.annotate import ACCENT, INK, PINNED
from .record import ToolRun
from ..desktop.actions import Outcome

# Numbered sequences are their own group, so numbering a second one replaces
# the first. Two sets of digits on the same screen is worse than none.
STEP_BADGES = "step-badges"


def build(harness) -> list:
    """The tools in this module, bound to one harness."""

    @tool
    def look_at_screen(what_to_look_for: str = "") -> str:
        """LOOK at the screen and describe what is actually there.

        Use before teaching or advising about anything the control list does
        not cover - a chess position, a diagram, a video timeline, a photo, a
        game. The control list describes the WINDOW, not the page inside it:
        on chess.com it lists Chrome's tabs and buttons and says nothing about
        the board.

        Say what you are looking for - "the chess position", "which pieces are
        where" - and you get a description of what is on screen.
        """
        from ..platform.capture import capture_screens

        # Already looking. While a lesson is live the turn CARRIES a picture
        # of their screen, so calling this is paying 14.6 seconds to be told
        # what is already in front of the model - measured, with the seeing
        # model, on a plain VS Code window. The tool was the only way to see
        # before, and during a lesson it is now the slow way.
        if harness.watching and harness.saw_the_screen:
            harness.runs.append(ToolRun(
                "look_at_screen", what_to_look_for,
                Outcome(True, "already attached", method="attached")))
            return ("You can already see their screen - the picture above "
                    "this turn IS it, taken just now. Answer from that. Do "
                    "not say you are going to look.")

        harness.note("let me look at your screen.")
        shots = capture_screens()
        if not shots:
            return "I cannot see the screen."

        seen = harness.describe_screen(shots[0], what_to_look_for)
        harness.runs.append(ToolRun("look_at_screen", what_to_look_for,
                                    Outcome(bool(seen), "looked")))
        if not seen:
            return "I looked, but could not make anything out."
        # Returned as observation, not instruction. It is a description of
        # the user's own screen, which may contain anything at all.
        return (f"What is on their screen right now:{chr(10)}{seen}{chr(10)}"
                f"{chr(10)}Answer from THIS, not from memory. If they asked "
                f"where something is, follow up with show_on_screen so they "
                f"can see it marked.")

    @tool
    def show_on_screen(description: str, shape: str = "rings",
                       label: str = "") -> str:
        """Draw a mark on screen around something, so the user can SEE it.

        Use whenever explaining where a thing is - "the white queen", "the
        razor tool", "the left kidney on this diagram". Works on pictures and
        canvases where there are no controls at all.

        shape, and when each one is right:
          rings      concentric rings on a POINT. The default, and the honest
                     one when the thing has no obvious edges - a piece on a
                     board, an icon in a crowded toolbar.
          circle     one ring sized to the thing. For something with a clear
                     round-ish extent.
          box        a rectangle round it. For panels, fields, table cells.
          ellipse    an oval round it, for a wide or tall region.
          highlight  a translucent wash over it. For a region to READ, like a
                     paragraph or a row of settings.
          spotlight  dims the whole screen except this. For "ignore all of
                     that, look here" on a dense interface. Use it sparingly -
                     it covers their work.
          arrow      an arrow coming in to it from below left.

        label: optional words to write beside it, joined to the thing by a
        line - "the razor tool", "this is the keyframe". Say what it IS, never
        where it is.
        """
        # Said BEFORE the slow part. Grounding by sight is two API round
        # trips and takes about seven seconds, and seven seconds of silence
        # after a question reads as broken rather than as looking. The mark
        # appears when it lands; this covers the wait.
        harness.note(f"let me find {description} on your screen.")

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
        centre = target.centre
        pad = 18
        sketch = board.sketch

        if shape == "box":
            sketch.box(left - pad, top - pad, right + pad, bottom + pad)
        elif shape == "ellipse":
            sketch.ellipse(left - pad, top - pad, right + pad, bottom + pad)
        elif shape == "highlight":
            sketch.highlight(left - pad, top - pad, right + pad, bottom + pad)
        elif shape == "spotlight":
            # Generous padding: a spotlight cropped tight to a control hides
            # the thing next to it that gives it meaning.
            sketch.spotlight([(left - pad * 3, top - pad * 2,
                               right + pad * 3, bottom + pad * 2)])
        elif shape == "arrow":
            # Comes in from below-left, so the head does not sit on top of the
            # thing it is indicating.
            sketch.arrow((centre[0] - 180, centre[1] + 140), centre, bow=0.25)
        elif shape == "circle":
            sketch.circle(centre, max(28, (right - left) // 2 + pad))
        else:
            # Rings are the default, and that is a claim about honesty rather
            # than taste. Grounding by sight is right to within about thirty
            # pixels when it is right at all, and a circle drawn tight round a
            # rectangle asserts an extent that was never measured. Rings say
            # "this point", which is what was actually found.
            sketch.rings(centre, max(30, (right - left) // 2 + pad))

        if label:
            # Offset below and left of the thing, so the plate does not cover
            # what it is naming, with a leader back to it.
            sketch.label((centre[0] - 150, centre[1] + 110), label,
                         leader=centre, colour=ACCENT)
        # NOT drawn here. A Win32 window belongs to the thread that
        # made it, tools run on a per-turn worker, and painting
        # from another thread onto a window whose creator has
        # exited is WinError 1400. The render loop paints.

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
        harness.note("let me look at your screen.")
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
        board.sketch.rings(start.centre, 34, colour=ACCENT, seconds=8.0)
        board.sketch.arrow(start.centre, end.centre, bow=0.28,
                           colour=INK, seconds=8.0)
        harness.runs.append(ToolRun(
            "draw_a_move", f"{from_description} -> {to_description}",
            Outcome(True, "drew the move"), end))
        return (f"Drawn an arrow from {from_description} to "
                f"{to_description}. Say what the move is, not where it is.")

    @tool
    def number_the_steps(things: list[str]) -> str:
        """Number several things on screen, in the order they are to be used.

        For teaching a sequence that lives in ONE window - "click the fill
        tool, then the colour swatch, then the canvas". Draws a numbered
        badge on each, so the whole order is visible at once instead of
        being held in their head.

        Not for a route through menus and pages: those are steps on different
        screens, and find_how_to walks them one at a time. This is for things
        that are all in front of them now.

        Up to four. Each one has to be found on screen, which can take several
        seconds apiece when the accessibility tree cannot see them.
        """
        wanted = [thing for thing in things if thing and thing.strip()][:4]
        if len(wanted) < 2:
            return ("Numbering needs at least two things. For one, use "
                    "show_on_screen.")

        board = harness.board()
        if board is None:
            return "I cannot draw on this screen."

        harness.note(f"let me find those {len(wanted)} things on your screen.")

        # Cleared as a GROUP, so numbering a second sequence replaces the
        # first instead of leaving two sets of digits on screen.
        board.sketch.clear(group=STEP_BADGES)

        found: list[str] = []
        missing: list[str] = []
        for position, thing in enumerate(wanted, start=1):
            target = harness.locate_anything(thing)
            if target is None:
                missing.append(thing)
                continue
            board.sketch.rings(target.centre, 30, seconds=PINNED,
                               group=STEP_BADGES)
            # Offset up and right of the ring, so the badge does not cover
            # the thing it is numbering.
            badge = (target.centre[0] + 34, target.centre[1] - 32)
            board.sketch.number(badge, position, colour=ACCENT,
                                seconds=PINNED, group=STEP_BADGES)
            found.append(thing)

        harness.runs.append(ToolRun(
            "number_the_steps", ", ".join(wanted)[:60],
            Outcome(bool(found), f"numbered {len(found)} of {len(wanted)}")))

        if not found:
            board.sketch.clear(group=STEP_BADGES)
            return ("I could not find any of those on this screen, so I have "
                    "drawn nothing. Do not guess at where they are.")
        numbered = ", ".join(f"{n}" for n in range(1, len(found) + 1))
        answer = (f"Numbered {numbered} on their screen: "
                  f"{'; '.join(found)}. Say what to do in that order, "
                  f"referring to the numbers. The marks stay until cleared.")
        if missing:
            answer += (f" NOT found, so not numbered: {'; '.join(missing)} - "
                       f"say so rather than describing where they might be.")
        return answer

    @tool
    def teach_me_this(steps: list[str]) -> str:
        """Walk the user through a procedure, ONE STEP AT A TIME.

        Use this whenever somebody asks to be TAUGHT how to do something
        that takes more than one action - "teach me how to animate a
        bouncing ball", "show me how to add a modifier", "how do i export
        this". Write the steps yourself, from what you know about the
        application. You know these; that is not what you need a tool for.

        What you need it for is the PACING. Said in one breath, four steps
        are a recitation - the user cannot hold them and find them at the
        same time, which is the whole reason they asked. This says the first
        one, waits, and says the next when they tell you they have done it.

        Each step is one thing to DO, in their own hands, written for the
        ear: "press shift a and choose mesh, then uv sphere". Not "first,
        you will want to" - just the action. Two to eight of them.

        Say nothing after calling this. The first step is said for you.
        """
        wanted = [step for step in steps if step and step.strip()]
        if len(wanted) < 2:
            return ("That is one step, so just say it. This is for a "
                    "procedure somebody has to be walked through.")

        if harness.start_teaching is None:
            # No app to drive the pacing - a harness running standalone, or
            # a background task. Fall back to saying them, which is worse
            # than teaching and better than silence.
            return ("I cannot pace this here, so say the steps in order, "
                    "briefly: " + "; ".join(wanted))

        started = harness.start_teaching(wanted)
        harness.runs.append(ToolRun(
            "teach_me_this", f"{len(wanted)} steps",
            Outcome(started, "teaching" if started else "could not start")))
        if not started:
            return ("I cannot pace this here, so say the steps in order, "
                    "briefly: " + "; ".join(wanted))
        # Live, the model said the first step anyway, straight after the
        # app had said it: "...tell me when you have." / "press shift a and
        # choose mesh, then uv sphere." Telling it to say nothing is a
        # request, and the reply after a tool call is where a model most
        # wants to be helpful. So it is given something harmless to say
        # instead of being asked for silence it will not produce.
        return (f"Started, and the user has ALREADY HEARD: "
                f"\"{wanted[0]}\" - word for word, out loud, just now. "
                f"Reply with AT MOST a short acknowledgement that adds "
                f"something they do not already know - why this step, or "
                f"what they will see. Never restate the step. Never list "
                f"the remaining {len(wanted) - 1}. If you have nothing to "
                f"add, reply with exactly: ok")

    @tool
    def clear_the_screen() -> str:
        """Rub out every mark drawn so far."""
        board = harness.board()
        if board is not None:
            # Marks only; the render thread notices they have gone.
            board.sketch.clear()
        harness.runs.append(ToolRun("clear_the_screen", "",
                                    Outcome(True, "cleared")))
        return "Cleared the marks."

    return [
        look_at_screen,
        show_on_screen,
        draw_a_move,
        number_the_steps,
        teach_me_this,
        clear_the_screen,
    ]
