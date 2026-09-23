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


@pytest.mark.parametrize("said, expected", [
    ("reply to that email", "send mail"),
    ("send a mail to arun", "send mail"),
    ("check my mail", "send mail"),
    ("book a meeting on thursday", "calendar"),
    ("schedule an interview on the 25th", "calendar"),
    ("research gpu prices and put it in a spreadsheet", "put it in a file"),
    ("write a report on solar panels", "put it in a file"),
    ("copy that into notepad", "copy text"),
    ("paste it into word", "copy text"),
])
def test_the_workflow_recipes_match_their_work(shelf, said, expected):
    """Multi-step jobs where the ORDER is the knowledge - find out then write,
    draft then approve, switch then type.
    """
    found = shelf.find(said)
    assert found, f"{said!r} matched no recipe"
    assert expected in found[0].title, f"{said!r} -> {found[0].title!r}"


@pytest.mark.parametrize("said", [
    "minimise this window", "close that window", "click save", "open notepad",
])
def test_a_generic_word_in_a_title_does_not_drag_a_recipe_in(shelf, said):
    """Third time this pattern bit: a title written as a sentence donates its
    words as triggers. "take what is in one window and put it in another" gave
    away "window" and matched "minimise this window". "window" cannot be
    filler - new-document needs the phrase "new window" - so the title lost
    the word instead.
    """
    found = shelf.find(said)
    assert not any("copy text" in recipe.title for recipe in found), \
        f"{said!r} pulled in {[r.title for r in found]}"


def test_a_request_can_match_two_recipes(shelf):
    """"Move it to excel" is both an Excel question and a copy-between-apps
    question, and the model is better off with both than with a guess about
    which one was meant.
    """
    found = shelf.find("move it to excel")
    assert len(found) == 2


@pytest.mark.parametrize("said", [
    "open youtube and search campus x there",
    "which profile", "open the watermelon profile", "go to youtube",
])
def test_the_chrome_recipe_covers_the_profile_picker(shelf, said):
    """Chrome opened on "Who's using Chrome?" and the plan pressed ctrl+t,
    typed, and pressed Enter into a window with no tab strip and no address
    bar. All three correctly reported "nothing changed visibly"; the plan ran
    to the end anyway and then described what it had done.
    """
    found = shelf.find(said)
    assert found, f"{said!r} matched no recipe"
    assert "Chrome" in found[0].title


@pytest.mark.parametrize("said, expected", [
    ("how do i change dark mode to light mode",
     ["Personalization", "Colors"]),
    ("i dont know how to change dark mode", ["Personalization", "Colors"]),
    ("how do i change my dns",
     ["Network & internet", "Advanced network settings"]),
    ("where is bluetooth", ["Bluetooth & devices", "Devices"]),
])
def test_a_written_route_beats_a_mined_one(shelf, said, expected):
    """Mining a route off a web page is unreliable in both directions: "how do
    i change dark mode" mined "Theme, Display" - plausible, and not the path -
    and "how do i change my dns" mined nothing at all. A route somebody wrote
    down is local, free, trusted and correct.
    """
    assert shelf.route_for(said) == expected


def test_no_route_for_something_nobody_wrote_one_for(shelf):
    """It must not invent a path, or find_how_to would stop looking things up."""
    assert shelf.route_for("how do i open notepad") == []
    assert shelf.route_for("what is the capital of france") == []


def test_the_most_specific_written_route_wins():
    recipe = recipes.parse(
        "# a thing\n"
        "when: alpha\n"
        "route: dns = Network > Advanced\n"
        "route: dns server address = Network > Advanced > DNS assignment\n"
        "\n"
        "Body.\n")
    shelf = recipes.Shelf(recipes=[recipe])
    assert shelf.route_for("change my dns") == ["Network", "Advanced"]
    assert shelf.route_for("set the dns server address") == [
        "Network", "Advanced", "DNS assignment"]


def test_a_route_line_is_not_left_in_the_body():
    """Or it reads as prose when the recipe is put in front of the model."""
    recipe = recipes.parse(
        "# a thing\n"
        "when: alpha\n"
        "route: dark mode = Personalization > Colors\n"
        "\n"
        "The real body.\n")
    assert "route:" not in recipe.body
    assert recipe.body.strip() == "The real body."
