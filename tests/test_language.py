"""What a sentence means, before anything acts on it.

Pure strings in, decisions out - no Windows, no keys, no network. These ran as
throwaway one-liners all through the session that produced them; every case
here is one that was actually wrong once.
"""

from __future__ import annotations

import pytest

from meow.language import (
    hears_yes,
    is_noise,
    spoken_words,
    wants_its_own_window,
    without_trailing_yes_no,
)
from meow.language.routing import correct, needs_a_connected_account
from meow.agent.router import Intent, Route


@pytest.mark.parametrize("said, expected", [
    ("Yeah, close it.", "yeah close it"),
    # An apostrophe becomes a SPACE, not nothing, so "what's" is two tokens.
    # Harmless for every match that depends on it - "inbox" is still a word -
    # but it inflates word counts slightly, which MINIMUM_WORDS_FOR_A_PLAN
    # reads. Recorded here rather than changed: a refactor that alters
    # matching behaviour is not a refactor.
    ("What's in my inbox?", "what s in my inbox"),
    ("  MULTIPLE   spaces ", "multiple spaces"),
    ("", ""),
])
def test_punctuation_is_stripped_before_matching(said, expected):
    # "close it" is not inside "close it." because of the full stop, which is
    # why saying "close it" closed Notepad instead of dismissing a task.
    assert spoken_words(said) == expected


@pytest.mark.parametrize("said", ["Oh.", "um", "hmm", "yeah", "uh huh", ""])
def test_breaths_are_not_requests(said):
    # "Oh." was routed to plan and handed its own background window.
    assert is_noise(said)


@pytest.mark.parametrize("said", [
    "stop",                      # one word, but a real instruction
    "open notepad and type hi",
    "oh can you open notepad",   # opens with filler, is not filler
])
def test_real_instructions_survive(said):
    assert not is_noise(said)


def test_one_word_is_not_a_task_in_any_language():
    # A Hindi "haan" was routed to plan and given a background window. No
    # English filler list would ever have caught it; the structural rule does.
    assert is_noise("haan")
    assert is_noise("हाँ")


@pytest.mark.parametrize("said, expected", [
    ("yes", True), ("go ahead", True), ("do it", True),
    ("no", False), ("nope", False), ("leave it", False),
    ("what time is it", None),
])
def test_agreement(said, expected):
    assert hears_yes(said) is expected


def test_a_trailing_yes_no_question_is_dropped():
    # Live, "would you like to see it?" collected a bare "yes" and retyped a
    # whole paragraph. A bare yes carries no instruction.
    said = "i wrote a brief piece about elon musk. would you like to see it?"
    assert without_trailing_yes_no(said) == "i wrote a brief piece about elon musk."


def test_a_reply_that_is_only_a_question_is_kept():
    # Swallowing it would leave silence, which is worse than the question.
    said = "would you like to see it?"
    assert without_trailing_yes_no(said) == said


@pytest.mark.parametrize("said, wants_window", [
    ("research gpu prices in india", True),
    ("make a deck about the history of computing", True),
    ("click the save button", False),
    ("minimise this window", False),
])
def test_only_work_you_walk_away_from_gets_a_window(said, wants_window):
    # Decided by what the job NEEDS, not by sentence length: a four-step
    # desktop job was once handed a window to narrate what was already
    # on screen.
    assert wants_its_own_window(said) is wants_window


@pytest.mark.parametrize("said", [
    "what's in my inbox", "on my calendar?", "what's the weather in delhi",
    "what's on hacker news", "what's on my todo list",
])
def test_questions_only_a_service_can_answer(said):
    assert needs_a_connected_account(said)


@pytest.mark.parametrize("said", [
    "what is the capital of france", "open notepad", "click the close button",
])
def test_ordinary_questions_are_left_alone(said):
    assert not needs_a_connected_account(said)


def test_an_inbox_question_reaches_the_harness():
    # It is shaped exactly like a question, so Jev calls it ANSWER - and the
    # answer path holds no tools, so the cat talked about the screenshot it
    # had been handed instead.
    route, why = correct(Route(Intent.ANSWER, True, False, "jev"),
                         "what's in my inbox")
    assert route.intent is Intent.ACT
    assert why


def test_making_a_file_about_a_topic_is_a_plan():
    route, why = correct(Route(Intent.ACT, False, False, "jev"),
                         "gpu prices in india, put it in a spreadsheet")
    assert route.intent is Intent.PLAN


def test_asking_how_is_never_upgraded_to_doing():
    # SHOW is somebody asking how to do a thing themselves. Turning that into
    # an action is the one correction that would be actively rude.
    route, why = correct(Route(Intent.SHOW, False, False, "jev"),
                         "how do i check my mail")
    assert route.intent is Intent.SHOW
    assert why == ""


def test_make_a_spreadsheet_is_not_a_connector_question():
    # "document" and "spreadsheet" are kept OUT of CONNECTOR_WORDS: promoting
    # them to ACT would skip the artefact rule that makes this a plan.
    assert not needs_a_connected_account("make me a spreadsheet about gpus")


@pytest.mark.parametrize("said", [
    "do a research on gpu prices in india",
    "look up the best laptops under fifty thousand",
    "find out what time the shop closes",
    "google the population of brazil",
])
def test_being_asked_to_look_something_up_reaches_a_tool(said):
    """The answer path holds no tools, so a research question routed to ANSWER
    is answered from training data: confident, fluent, a year out of date, and
    with nothing in the reply to suggest it never looked.
    """
    route, why = correct(Route(Intent.ANSWER, False, False, "jev"), said)
    assert route.intent is Intent.ACT, f"{said!r} stayed {route.intent}"
    assert why


@pytest.mark.parametrize("said", [
    "search for it in chrome",
    "click the address bar and search for cats",
])
def test_naming_the_desktop_keeps_it_on_the_desktop(said):
    """Driving somebody's browser is a different act that happens to use the
    same words as asking what something is.
    """
    route, _ = correct(Route(Intent.ANSWER, False, False, "jev"), said)
    assert route.intent is Intent.ANSWER


@pytest.mark.parametrize("said, expected", [
    ("what is in my inbox", Intent.ACT),
    ("show me my inbox", Intent.ACT),
    ("whats on my calendar", Intent.ACT),
    ("how do i check my mail", Intent.SHOW),
    ("show me how to check my mail", Intent.SHOW),
    ("where is the bluetooth setting", Intent.SHOW),
    ("take me to my calendar settings", Intent.SHOW),
])
def test_asking_how_stays_show_and_asking_what_does_not(said, expected):
    """Jev called "what's in my inbox" SHOW on one run and ANSWER on another -
    genuinely ambiguous read as a sentence. SHOW refuses every tool that could
    reach an account, so that run answered with a route nobody asked for. The
    distinction is method versus content, and it is a real one.
    """
    route, _ = correct(Route(Intent.SHOW, False, False, "jev"), said)
    assert route.intent is expected, f"{said!r} -> {route.intent}"
