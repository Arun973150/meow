"""Reading the words on screen.

Pure: a fake reading in, lines and a prompt block out. No PowerShell, no
screenshots.

The measured result this file encodes is two-sided, and the sides disagree:

    as GROUNDING   4/44 on the hand-labelled targets, 29% precision, and the
                   HIGHEST scoring answer is a miss - "blade edit mode"
                   matched the text "Blade Edit Mode" exactly and pointed
                   somewhere the user did not mean
    as a READER    128 words from a live Blender in 801ms, where the
                   accessibility tree returns five - Minimize, Maximize,
                   Close, System, System

So it is wired in as words for a blind window and NOT as a way to point.
"""

from __future__ import annotations

import pytest

from meow.desktop import ocr
from meow.desktop.ocr import Reading, Word


def word(text, left=0, top=0, line=None, width=None):
    width = width if width is not None else len(text) * 7
    return Word(text=text, left=left, top=top, right=left + width,
                bottom=top + 10, line=line if line is not None else text)


def blender_ish():
    """A reading shaped like the real one, damage included."""
    menu = "File Edit Render Window Help"
    return Reading(width=1920, height=1200, words=[
        word("File", 10, 4, menu), word("Edit", 50, 4, menu),
        word("Render", 90, 4, menu), word("Window", 140, 4, menu),
        word("Help", 200, 4, menu),
        # As it really comes back: the mode, split.
        word("v", 10, 40, "v Object Mod e"),
        word("Object", 20, 40, "v Object Mod e"),
        word("Mod", 70, 40, "v Object Mod e"),
        word("e", 100, 40, "v Object Mod e"),
        word("Scale", 10, 80, "Scale X 1.000"),
        word("X", 60, 80, "Scale X 1.000"),
        word("1.000", 80, 80, "Scale X 1.000"),
        word("Cube", 10, 120, "Cube"),
        # Read noise: a stray mark as a one-character word.
        word("o", 10, 160, "o"),
        word("I", 30, 160, "I"),
    ])


# --- what it gives a turn ----------------------------------------------------


def test_lines_are_distinct_and_in_reading_order():
    found = ocr.lines_of(blender_ish())
    assert found[0] == "File Edit Render Window Help"
    assert "v Object Mod e" in found
    assert found.count("v Object Mod e") == 1, "one line per line"


def test_read_noise_is_dropped():
    """The engine emits stray marks as one-character words, and a dense
    interface has dozens.
    """
    found = ocr.lines_of(blender_ish())
    assert "o" not in found and "I" not in found


def test_LINES_not_words_because_a_word_alone_has_lost_its_meaning():
    """"Scale" is a panel heading, a transform tool and a property.
    "Scale X 1.000" is a value.
    """
    assert "Scale X 1.000" in ocr.lines_of(blender_ish())


def test_the_block_says_it_is_EVIDENCE_and_not_a_quotation():
    """The engine garbles small text, so a model that repeats a line verbatim
    says something that is not on screen - and one that treats the damage as
    a wrong answer throws away the only description of the window.
    """
    block = ocr.describe(blender_ish(), "blender.exe")
    assert "EVIDENCE" in block
    assert "garbled" in block
    assert "Object Mod e" in block, "the example of damage has to be concrete"
    assert "blender.exe" in block


def test_the_block_says_the_tree_cannot_see_this_window():
    block = ocr.describe(blender_ish())
    assert "accessibility tree cannot see" in block


def test_nothing_read_is_silence():
    assert ocr.lines_of(Reading()) == []
    assert ocr.describe(Reading()) == ""
    assert ocr.describe(Reading(error="powershell failed")) == ""


def test_a_wall_of_prose_is_capped():
    """A text editor full of words is not an interface description, and the
    whole argument for this is that it is cheap.
    """
    many = Reading(words=[word(f"line number {n}", 0, n * 10) for n in range(400)])
    assert len(ocr.lines_of(many)) == ocr.MAX_LINES


def test_the_block_is_far_cheaper_than_the_picture():
    """Measured: 240-363 tokens against 2,833 flat for the low-detail image
    of the same window. That is the reason it is worth having at all.
    """
    block = ocr.describe(blender_ish(), "blender.exe")
    assert len(block) // 4 < 2833


# --- finding words in it, which is NOT the same as pointing ------------------


def test_adjacent_words_score_above_a_scattered_match():
    """Scored on a RUN of adjacent words, not on the line. A line is a visual
    row, and a menu bar is one row of nine unrelated items - penalising a
    long row scored Photoshop's real Filter menu at 0.25 and a "Filter
    Filter Recent Files" panel at 1.50.
    """
    found = ocr.find("object mode", blender_ish())
    assert found, "nothing matched"
    _score, _box, text = found[0]
    assert "Object" in text


def test_covering_the_whole_request_beats_covering_part():
    reading = Reading(words=[
        word("Colour", 0, 0, "Colour Mode"), word("Mode", 50, 0, "Colour Mode"),
        word("Mode", 0, 40, "Mode"),
    ])
    best = ocr.find("colour mode", reading)[0]
    assert best[2] == "Colour Mode"


def test_the_box_is_the_RUN_not_the_whole_row():
    """A hit on "Render" in a menu bar must point at Render, not at the
    middle of the row.
    """
    found = ocr.find("render", blender_ish())
    _score, box, _text = found[0]
    assert box[0] >= 90 and box[2] <= 140


def test_a_tie_goes_to_the_SMALLER_box():
    """A tight run round the words asked for is a better answer than a wide
    one that happens to contain them.
    """
    reading = Reading(words=[
        word("Filter", 0, 0, "Filter"),
        word("Filter", 0, 40, "Filter Filter Recent"),
        word("Filter", 60, 40, "Filter Filter Recent"),
        word("Recent", 120, 40, "Filter Filter Recent"),
    ])
    _score, box, _text = ocr.find("filter", reading)[0]
    assert box == (0, 0, 42, 10)


def test_filler_alone_never_matches():
    assert ocr.find("show me the thing", blender_ish()) == []
    assert ocr.find("", blender_ish()) == []


def test_nothing_matches_an_unrelated_request():
    assert ocr.find("razor tool", blender_ish()) == []


@pytest.mark.parametrize("broken", [
    Reading(),
    Reading(error="no engine"),
    Reading(words=[]),
])
def test_a_failed_reading_is_survived(broken):
    assert ocr.find("anything", broken) == []
    assert ocr.describe(broken) == ""
