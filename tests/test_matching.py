"""Finding the control somebody meant, and admitting when several fit.

The worst failure available in this project is not missing a control - it is
pointing confidently at the wrong one. A miss is visible and recoverable; a
wrong click is neither.
"""

from __future__ import annotations

import pytest

from meow.desktop.lookup import already_on_screen
from meow.desktop.uia import Element, Regime, WindowDigest


def digest(*names):
    return WindowDigest(
        app="test.exe", title="t",
        elements=[Element(name=n, role="Button", left=0, top=0,
                          right=10, bottom=10) for n in names],
        regime=Regime.RICH, total_found=len(names), usable_found=len(names),
        query_seconds=0.0)


PICKER = ("AI profile", "arolabs.ai profile", "arrayofhope profile",
          "Arun profile", "arunn5189@gmail.com profile", "linelens profile",
          "msrit.edu profile", "Guest mode")


def test_equal_matches_are_not_resolved_by_picking_the_first():
    """Live: "open the water profile" against Chrome's picker. Every card tied
    on the word "profile", `score > best_score` let the first one win, and the
    cat announced "i'm pointing at the arunn5189@gmail.com profile" with no
    hint it had chosen between eight equals.
    """
    picker = digest(*PICKER)
    assert picker.find("the water profile") is None
    assert len(picker.rivals("the water profile")) > 1


def test_a_specific_request_still_resolves():
    """The guard must not make everything ambiguous."""
    picker = digest(*PICKER)
    assert picker.find("arun profile").name == "Arun profile"
    assert picker.find("guest mode").name == "Guest mode"
    assert picker.rivals("guest mode") == []


@pytest.mark.parametrize("said, expected", [
    ("minimize", "Minimize"),
    ("minimise", "Minimize"),          # British spelling, no shared word
    ("close", "Close"),
    ("that terminal thing", "Terminal (Ctrl+`)"),
])
def test_ordinary_speech_still_reaches_the_control(said, expected):
    window = digest("Minimize", "Maximize", "Close", "Terminal (Ctrl+`)")
    found = window.find(said)
    assert found is not None and found.name == expected


@pytest.mark.parametrize("said", [
    "guide me towards like how to minimize the vs code",
    "can you teach me how to minimize the vs code",
    "show me how to minimize this",
])
def test_the_same_request_phrased_differently_finds_the_same_thing(said):
    """The bug that prompted this. One phrasing searched the WEB, returned a
    route about minimising to the system tray, and said none of it was on
    screen - while the Minimize button sat in the title bar. The other
    pointed at the button. Same request, opposite answers, decided by which
    tool the model happened to reach for.
    """
    window = digest("Minimize", "Maximize", "Close", "Explorer")
    found = already_on_screen(said, window)
    assert found is not None and found.name == "Minimize"


def test_something_genuinely_absent_falls_through_to_the_web():
    """already_on_screen must not invent a local match, or find_how_to would
    never look anything up.
    """
    window = digest("Minimize", "Maximize", "Close", "Explorer")
    assert already_on_screen(
        "how do i change the dark mode of my screen", window) is None
    assert already_on_screen("where is my dns setting", window) is None


# --- one spelling, and the strict lookup has to fold it too -----------------
#
# `risk.py` has folded -ise/-ize since the confirmation gate asked permission
# to minimise a window somebody had just said to minimise. The identical
# comparison in `lookup.already_on_screen` did NOT, so "where is the minimise
# button" missed the Minimize button in the title bar, fell through to
# grounding by sight, spent 6.76 seconds there and then said it was not sure -
# while the answer sat in the digest the whole time.


def a_digest(*names):
    from meow.desktop.uia import Element, Regime, WindowDigest

    return WindowDigest(
        app="Code.exe", title="a window", regime=Regime.RICH,
        total_found=len(names), usable_found=len(names), query_seconds=0.1,
        elements=[Element(name=name, role="button", left=index * 40, top=0,
                          right=index * 40 + 30, bottom=20)
                  for index, name in enumerate(names)])


def test_one_spelling_lives_in_ONE_place():
    """Two copies of a spelling rule drift, and the half that has it looks
    exactly like the half that does not.
    """
    from meow.agent import risk
    from meow.language.phrases import one_spelling

    for word in ("minimise", "maximise", "analyse", "organisation",
                 "minimize", "close", "save"):
        assert risk._spelling(word) == one_spelling(word)


def test_the_strict_lookup_folds_british_spelling():
    from meow.desktop.lookup import already_on_screen

    screen = a_digest("Minimize", "Maximize", "Close", "Analyze data")
    for said, wanted in (
            ("where is the minimise button", "Minimize"),
            ("where is the minimize button", "Minimize"),
            ("how do i maximise this", "Maximize"),
            ("where is the analyse data button", "Analyze data"),
    ):
        found = already_on_screen(said, screen)
        assert found is not None, f"{said!r} matched nothing"
        assert found.name == wanted, f"{said!r} -> {found.name}"


def test_folding_does_not_make_the_strict_tier_fuzzy():
    """It is still EXACT matching. A spelling variant is one-to-one, not the
    word-overlap degradation this function exists to refuse.
    """
    from meow.desktop.lookup import already_on_screen

    screen = a_digest("Minimize", "Settings and more", "Profile")
    # "dark mode" is nowhere in the tree and must still fall through.
    assert already_on_screen("how do i change dark mode", screen) is None
    # A word that merely appears inside a longer name is not a match.
    assert already_on_screen("where is more", screen) is None


def test_two_controls_with_the_same_folded_name_is_a_QUESTION():
    """Picking one is how the cat pointed confidently at the wrong profile."""
    from meow.desktop.lookup import already_on_screen

    screen = a_digest("Minimize", "Minimise")
    assert already_on_screen("where is the minimise button", screen) is None
