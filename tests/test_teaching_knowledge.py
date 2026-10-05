"""What it knows versus what it has to look up.

The live failure this file exists for: Blender open, "how do i do a simple
animation of a ball jumping on a single plane", and the answer was

    "i can't find specific instructions for that in blender right now.
     it might be in a different window or version."

The model knows how to animate a bouncing ball - it has read every tutorial
ever written. A note about Blender was in the prompt. What it did instead was
search the web, fail, and report the failure, because GUIDE_REMINDER said
"You MUST use a tool. Never describe a location from your own knowledge" -
a rule written to stop it inventing BUTTON POSITIONS, over-applied to
PROCEDURES, where the model is good.
"""

from __future__ import annotations

import pytest

from meow.desktop.uia import Element, Regime, WindowDigest
from meow.knowledge.recipes import Shelf, parse
from meow.tools import knowledge as knowledge_tools


BLENDER_NOTE = """# work in Blender
app: blender, blender.exe
when: modifier, keyframe, animation

Press I to insert a keyframe. The spanner tab is Modifier Properties.
"""


def digest_for(app, title, *names):
    return WindowDigest(
        app=app, title=title,
        elements=[Element(name=n, role="Button", left=0, top=0,
                          right=9, bottom=9) for n in names],
        regime=Regime.RICH, total_found=len(names), usable_found=len(names),
        query_seconds=0.0)


class FakeHarness:
    """Only what the knowledge tools actually reach for."""

    def __init__(self, digest, recipes=()):
        self.digest = digest
        self.shelf = Shelf()
        for text in recipes:
            self.shelf.recipes.append(parse(text))
        self.runs = []
        self.last_directions = None
        self.notes = []
        self._researcher = None
        self._last_document = None

    def note(self, sentence):
        self.notes.append(sentence)

    def ghost_pointer(self):
        return None

    def _resolve(self, name):
        return None

    def _reader(self):
        return None

    def _explaining(self, _what):
        return ""


def find_how_to(harness):
    return next(tool for tool in knowledge_tools.build(harness)
                if tool.name == "find_how_to")


def test_a_note_about_this_application_answers_before_the_web(monkeypatch):
    """Searching past a skill to ask a search engine is how the answer ended
    up sitting in the prompt, unread.
    """
    def must_not_run(*_a, **_k):
        raise AssertionError("it searched the web with a note right there")

    monkeypatch.setattr("meow.desktop.lookup.ground", must_not_run)

    harness = FakeHarness(digest_for("blender.exe", "Blender", "Minimize"),
                          [BLENDER_NOTE])
    said = find_how_to(harness).invoke(
        {"question": "how do i animate a bouncing ball"})

    assert "note above" in said
    assert "Do not search the web" in said
    assert harness.runs[-1].outcome.ok


def test_a_note_for_a_DIFFERENT_application_does_not_short_circuit(monkeypatch):
    """The skill is scoped. In Notepad it describes an interface that is not
    on the screen, so the web is still the right next step.
    """
    reached = []

    class Result:
        grounded = False
        candidates = []
        directions = None

        def describe(self):
            return "nothing"

    monkeypatch.setattr("meow.desktop.lookup.ground",
                        lambda *a, **k: reached.append(a) or Result())

    harness = FakeHarness(digest_for("notepad.exe", "Untitled - Notepad",
                                     "Minimize"), [BLENDER_NOTE])
    find_how_to(harness).invoke({"question": "how do i animate a bouncing ball"})
    assert reached, "with no note for this app it must still look it up"


def test_failing_to_point_is_never_reported_as_not_knowing(monkeypatch):
    """The exact sentence from the live run. Not finding it on screen is a
    failure of the POINTING; the steps are still known and still wanted.
    """
    class Result:
        grounded = False
        directions = None

        class _Candidate:
            name = "Graph Editor"
        candidates = [_Candidate()]

        def describe(self):
            return "not grounded"

    monkeypatch.setattr("meow.desktop.lookup.ground", lambda *a, **k: Result())

    harness = FakeHarness(digest_for("notepad.exe", "Untitled", "Minimize"))
    said = find_how_to(harness).invoke({"question": "how do i add a keyframe"})

    assert "say what you know" in said
    assert "never a reason to claim you do not know how" in said


def test_the_guide_reminder_separates_knowing_from_locating():
    """One rule, and it is the whole of teaching: the HOW is yours, the
    WHERE is the screen's.
    """
    from meow.agent.harness import GUIDE_REMINDER

    assert "HOW to do something" in GUIDE_REMINDER
    assert "you know" in GUIDE_REMINDER
    assert "WHERE something is on THIS screen you do NOT know" in GUIDE_REMINDER
    # The old blanket ban is gone - it forbade the half the model is good at.
    assert "Never describe a location from your own knowledge" not in GUIDE_REMINDER


def test_an_empty_tool_result_is_not_an_empty_answer():
    from meow.agent.harness import GUIDE_REMINDER

    assert "failure of the POINTING" in GUIDE_REMINDER or \
           "could not mark it on screen" in GUIDE_REMINDER
