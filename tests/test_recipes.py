"""Recipes: the mechanism that makes "write a paragraph" a real answer.

Three bugs here were silent, and silent in the same way - a recipe that did
not load and a recipe whose triggers were half-read both look exactly like a
request that matched nothing. Nothing throws, nothing logs, the cat just
does not know the thing.
"""

from __future__ import annotations

import pytest

from meow.knowledge import recipes


@pytest.fixture(scope="module")
def shelf():
    return recipes.load()


def test_the_shipped_recipes_actually_load(shelf):
    """They vanished once already.

    `shipped_folder()` was `parent.parent / "recipes"`, correct while the
    module sat at meow/recipes.py and wrong the moment it moved to
    meow/knowledge/. Every shipped recipe disappeared and nothing said so.
    They live inside the package now, which is also the only place a wheel
    would carry them.
    """
    assert len(shelf.recipes) >= 5, f"only {len(shelf.recipes)} loaded"
    assert not shelf.skipped, f"unreadable recipes: {shelf.skipped}"


def test_a_wrapped_trigger_line_is_read_whole():
    """A good trigger list is longer than one line.

    Reading only the matched line threw the rest away: new-document.md lost
    "new file", "new note", "new sheet", "new workbook", "new slide" and "new
    deck" from the day it was written.
    """
    recipe = recipes.parse(
        "# a thing\n"
        "when: alpha, bravo,\n"
        "charlie, delta\n"
        "\n"
        "The body.\n")
    assert recipe is not None
    for word in ("alpha", "bravo", "charlie", "delta"):
        assert word in recipe.triggers, f"{word} was dropped"
    # And the continuation must not end up in the body as stray keywords.
    assert "charlie" not in recipe.body


def test_the_trigger_list_stops_at_a_blank_line():
    recipe = recipes.parse(
        "# a thing\n"
        "when: alpha, bravo\n"
        "\n"
        "The body mentions charlie.\n")
    assert "charlie" not in recipe.triggers
    assert "charlie" in recipe.body


@pytest.mark.parametrize("said", [
    "take a photo", "capture", "capture a photo", "snap a picture",
    "record a video", "take a video", "start recording", "stop recording",
    "shoot a video", "open the camera", "get a selfie", "film something",
])
def test_every_way_of_asking_for_the_camera_works(shelf, said):
    """The bug that prompted this: "take a photo" worked and "capture" did
    not, because the model had to make the leap from the words to the word
    "camera" itself, and did so inconsistently. Only the literal string
    "camera" ever resolved to the application.
    """
    found = shelf.find(said)
    assert found, f"{said!r} matched no recipe"
    assert "photo" in found[0].title, f"{said!r} matched {found[0].title!r}"


@pytest.mark.parametrize("said", [
    "open notepad", "what's in my inbox", "minimise this window",
    "what is the capital of france",
])
def test_unrelated_requests_match_nothing(shelf, said):
    """A recipe is injected into the prompt of every matching act turn, so a
    loose match is a recurring cost paid to mislead.
    """
    assert not shelf.find(said)


@pytest.mark.parametrize("said, expected", [
    ("open a new tab", "start something new"),
    ("open a new note", "start something new"),
    ("add a slide", "slide in PowerPoint"),
    ("go to a website", "website in Chrome"),
    ("where is the dark mode setting", "setting in Windows Settings"),
])
def test_the_other_recipes_still_match_what_they_should(shelf, said, expected):
    found = shelf.find(said)
    assert found, f"{said!r} matched no recipe"
    assert expected in found[0].title


def test_a_sentence_title_does_not_donate_generic_triggers(shelf):
    """The title counts as a trigger, which is useful until a title is written
    as a sentence: "start something new in an application" donated "start" and
    "something", and both matched anything. "start recording" and "film
    something" tied against it on exactly those words.
    """
    for said in ("start recording", "film something"):
        found = shelf.find(said)
        assert found and "photo" in found[0].title, \
            f"{said!r} went to {found[0].title if found else None!r}"


def test_a_half_written_file_is_skipped_not_guessed_at():
    assert recipes.parse("# title only\n") is None
    assert recipes.parse("no heading at all\n") is None
