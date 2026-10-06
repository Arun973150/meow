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


# --- the screen goes with the sentence, while a lesson is live --------------


class Budget:
    images_sent = 0
    images_skipped = 0


def harness_watching(monkeypatch, frames):
    """A harness that sees the given screens in order."""
    from meow.agent.harness import Harness

    harness = Harness.__new__(Harness)
    harness.watching = True
    harness._last_screen = None
    harness.budget = Budget()

    class Shot:
        def __init__(self, data):
            self._data = data

        def to_jpeg(self, quality=None):
            return self._data

    queued = [Shot(f) for f in frames]
    monkeypatch.setattr("meow.platform.capture.capture_screens",
                        lambda: [queued.pop(0)] if queued else [])
    return harness


def test_a_lesson_turn_carries_a_picture(monkeypatch):
    """"Yeah, I've added the ball now" is a sentence about the screen and
    nothing else. Without one the cat said "i can't see what you're talking
    about, tell me what's on your screen" - to somebody it had just sent off
    to add a ball.
    """
    harness = harness_watching(monkeypatch, [b"first-frame"])
    message = harness._with_the_screen("i've added the ball")

    assert isinstance(message.content, list)
    kinds = [part["type"] for part in message.content]
    assert "image_url" in kinds
    assert message.content[1]["image_url"]["detail"] == "low"


def test_an_unchanged_screen_is_words_rather_than_pixels(monkeypatch):
    """A lesson is many turns and a flat 2,833 tokens each would make
    teaching the most expensive thing here.
    """
    harness = harness_watching(monkeypatch, [b"same", b"same"])
    harness._with_the_screen("first")
    second = harness._with_the_screen("second")

    assert isinstance(second.content, str)
    assert "has not changed" in second.content
    assert harness.budget.images_skipped == 1
    assert harness.budget.images_sent == 1


def test_a_changed_screen_is_paid_for(monkeypatch):
    harness = harness_watching(monkeypatch, [b"before", b"after"])
    harness._with_the_screen("first")
    second = harness._with_the_screen("second")

    assert isinstance(second.content, list)
    assert harness.budget.images_sent == 2


def test_no_lesson_means_no_picture(monkeypatch):
    """The harness is normally given the control list and no image at all.
    Most turns need no pixels, and they are the whole cost of this project.
    """
    harness = harness_watching(monkeypatch, [b"anything"])
    harness.watching = False
    message = harness._with_the_screen("open notepad")

    assert isinstance(message.content, str)
    assert harness.budget.images_sent == 0


def test_a_screen_that_cannot_be_read_is_not_a_crash(monkeypatch):
    from meow.agent.harness import Harness

    harness = Harness.__new__(Harness)
    harness.watching = True
    harness._last_screen = None
    harness.budget = None
    monkeypatch.setattr("meow.platform.capture.capture_screens",
                        lambda: (_ for _ in ()).throw(OSError("no desktop")))
    assert isinstance(harness._with_the_screen("hello").content, str)


def test_a_turn_that_carries_a_picture_does_not_look_again(monkeypatch):
    """`look_at_screen` costs 14.6 seconds with the seeing model, measured on
    a plain VS Code window. During a lesson the turn already carries the
    screen, so calling it pays that to be told what the model is holding.
    """
    harness = harness_watching(monkeypatch, [b"a-frame"])
    harness._with_the_screen("what have i done")
    assert harness.saw_the_screen is True


def test_a_turn_without_one_is_free_to_look(monkeypatch):
    harness = harness_watching(monkeypatch, [b"a-frame"])
    harness.watching = False
    harness._with_the_screen("what is on my screen")
    assert harness.saw_the_screen is False


def test_an_unchanged_screen_still_counts_as_seen(monkeypatch):
    """It is one the model saw a moment ago and still has, which is just as
    good a reason not to spend fifteen seconds looking again.
    """
    harness = harness_watching(monkeypatch, [b"same", b"same"])
    harness._with_the_screen("first")
    harness._with_the_screen("second")
    assert harness.saw_the_screen is True


# --- at most ONE picture in the thread --------------------------------------


def human_with_picture(text="look at this"):
    from langchain_core.messages import HumanMessage

    return HumanMessage(content=[
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,xx",
                                            "detail": "low"}},
    ])


def has_picture(message) -> bool:
    return isinstance(message.content, list) and any(
        part.get("type") == "image_url" for part in message.content
        if isinstance(part, dict))


def test_only_the_newest_screenshot_survives():
    """A teaching turn carries a picture and the thread is permanent, so ten
    turns of being taught means ten screenshots recharged on every call -
    2,833 tokens each, which is seventeen thousand tokens of pictures of a
    screen that has since changed.
    """
    from meow.agent.harness import _one_picture_only

    messages = [human_with_picture("first"), human_with_picture("second"),
                human_with_picture("third")]
    kept, dropped = _one_picture_only(messages)

    assert dropped
    assert [has_picture(m) for m in kept] == [False, False, True]


def test_what_an_older_turn_SAID_is_kept():
    """Only the pixels go. What that turn said is still history."""
    from meow.agent.harness import _one_picture_only

    kept, _dropped = _one_picture_only(
        [human_with_picture("i added the ball"), human_with_picture("now what")])
    assert "i added the ball" in kept[0].content
    assert "no longer shown" in kept[0].content


def test_one_picture_is_left_alone():
    from meow.agent.harness import _one_picture_only

    messages = [human_with_picture("only one")]
    kept, dropped = _one_picture_only(messages)
    assert dropped is False
    assert kept is messages


def test_a_thread_with_no_pictures_is_untouched():
    from langchain_core.messages import HumanMessage

    from meow.agent.harness import _one_picture_only

    messages = [HumanMessage("open notepad"), HumanMessage("thanks")]
    kept, dropped = _one_picture_only(messages)
    assert dropped is False
    assert kept is messages


def test_unchanged_says_the_picture_is_still_above(monkeypatch):
    """Saying "has not changed" while dropping the picture is the bug mind.py
    already fixed once: the model, correctly given what it was handed,
    replies that it cannot see the screen.
    """
    harness = harness_watching(monkeypatch, [b"same", b"same"])
    harness._with_the_screen("first")
    second = harness._with_the_screen("second")
    assert "picture above" in second.content
