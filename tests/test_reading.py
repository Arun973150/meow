"""Reading the text of what is open, including what is scrolled away.

Every number in `meow/desktop/reading.py` was measured on this machine
against real windows. What is tested here is the SHAPE: the cap that makes
it affordable, the ranking that stops it returning the address bar, and the
honesty about being cut.
"""

from __future__ import annotations

import pytest

from meow.desktop import reading
from meow.desktop.reading import MOST_CHARACTERS


# --- fakes -------------------------------------------------------------------

class FakeRect:
    def __init__(self, width, height):
        self._width, self._height = width, height

    def width(self):
        return self._width

    def height(self):
        return self._height


class FakeRange:
    def __init__(self, text):
        self.text = text
        self.asked_for = []

    def GetText(self, wanted):
        self.asked_for.append(wanted)
        return self.text if wanted < 0 else self.text[:wanted]


class FakePattern:
    def __init__(self, text, watcher=None):
        self.DocumentRange = FakeRange(text)
        if watcher is not None:
            watcher.append(self.DocumentRange)

    def GetSelection(self):
        return [FakeRange("the selected words")]

    def GetVisibleRanges(self):
        # Measured at 65,387ms on a flattened Electron window. Nothing may
        # reach for it.
        raise AssertionError("GetVisibleRanges must not be used")


class FakeElement:
    def __init__(self, role, name, text=None, children=(),
                 width=800, height=600):
        self.ControlTypeName = role
        self.Name = name
        self._text = text
        self._children = list(children)
        self.BoundingRectangle = FakeRect(width, height)
        self.reads = []

    def GetChildren(self):
        return self._children

    def GetTextPattern(self):
        if self._text is None:
            # The getter is declared per control CLASS, so an element
            # without one raises rather than returning None.
            raise AttributeError("no text pattern on this control")
        return FakePattern(self._text, self.reads)


def window_holding(*children, title="an app"):
    return FakeElement("WindowControl", title, children=list(children))


# --- the cap is the mechanism, not a courtesy -------------------------------

def test_the_read_is_BOUNDED_at_the_source():
    """GetText(12000) is 15ms where GetText(-1) is 383ms on a 439KB window,
    because the provider never materialises the rest. Asking for everything
    and slicing afterwards would cost the 383ms."""
    document = FakeElement("DocumentControl", "the document", text="x" * 500_000)

    got = reading.read(window=window_holding(document))

    assert got is not None
    assert len(got.text) == MOST_CHARACTERS
    assert got.cut is True
    assert document.reads, "it read through the pattern"
    assert max(document.reads[0].asked_for) == MOST_CHARACTERS + 1, (
        "one more than the cap, which is how 'there is more' is known "
        "without paying for all of it")


def test_a_short_document_is_not_reported_as_cut():
    got = reading.read(window=window_holding(
        FakeElement("DocumentControl", "notes", text="a short note")))

    assert got.cut is False
    assert "CUT" not in got.describe()
    assert "scrolled out of view" in got.describe()


def test_being_cut_is_SAID_so_nothing_claims_to_have_read_it_all():
    said = reading.read(window=window_holding(
        FakeElement("DocumentControl", "big", text="z" * 50_000))).describe()

    assert "CUT" in said
    assert "must not say it is all of it" in said


# --- the ranking, which is what the first measurement got wrong -------------

def test_the_ADDRESS_BAR_is_never_the_document():
    """The first measurement reported that Chrome exposes no page text. It
    does: the search was returning "Address and search bar", which holds the
    URL and looks exactly like a 51-character document."""
    got = reading.read(window=window_holding(
        FakeElement("EditControl", "Address and search bar",
                    text="claude.ai/chat/4de8923e", width=600, height=30),
        FakeElement("DocumentControl", "the page",
                    text="the real page content, thousands of characters"),
        title="Claude - Google Chrome"))

    assert "page content" in got.text
    assert "claude.ai" not in got.text


@pytest.mark.parametrize("label_first", [True, False])
def test_a_document_beats_a_label_whatever_the_order(label_first):
    label = FakeElement("TextControl", "Create split view",
                        text="Create split view", width=80, height=20)
    document = FakeElement("DocumentControl", "the page",
                           text="the actual article text")
    children = [label, document] if label_first else [document, label]

    got = reading.read(window=window_holding(*children))

    assert "actual article" in got.text


def test_the_biggest_candidate_wins_within_a_role():
    got = reading.read(window=window_holding(
        FakeElement("DocumentControl", "a tooltip", text="tiny",
                    width=40, height=20),
        FakeElement("DocumentControl", "the editor", text="the whole file",
                    width=1900, height=1100)))

    assert "whole file" in got.text


def test_a_window_with_nothing_readable_returns_None():
    """None rather than an empty Reading: "this window holds no text" is a
    different answer from "the document is empty", and for a browser it is
    the one that sends the model to the URL instead."""
    got = reading.read(window=window_holding(
        FakeElement("PaneControl", "canvas"),
        FakeElement("ButtonControl", "Close")))

    assert got is None


def test_whitespace_is_not_text():
    got = reading.read(window=window_holding(
        FakeElement("DocumentControl", "empty", text="   \n\n  \t ")))

    assert got is None


# --- the noise UIA pads a flattened window with -----------------------------

@pytest.mark.parametrize("raw, gone", [
    ("File￼￼Edit", "￼"),
    ("meow", ""),
    ("a​b", "​"),
])
def test_placeholders_and_icon_glyphs_are_dropped(raw, gone):
    """On a flattened Electron window these are a real share of the
    characters, and every one is a token that says nothing."""
    got = reading.read(window=window_holding(
        FakeElement("DocumentControl", "d", text=raw)))

    assert gone not in got.text


def test_runs_of_blank_lines_collapse_but_paragraphs_survive():
    got = reading.read(window=window_holding(
        FakeElement("DocumentControl", "d", text="one\n\n\n\n\ntwo\nthree")))

    assert got.text == "one\n\ntwo\nthree"


def test_the_selection_can_be_read_on_its_own():
    got = reading.read("selection", window=window_holding(
        FakeElement("DocumentControl", "d", text="the whole document")))

    assert got.text == "the selected words"
    assert "SELECTED" in got.describe()


def test_nothing_reaches_for_GetVisibleRanges():
    """The fake raises if anything does."""
    got = reading.read(window=window_holding(
        FakeElement("DocumentControl", "d", text="some text")))

    assert got.found_anything


# --- the URL, the only route into a page the text cannot cover --------------

def _browser_showing(value, title="Claude - Google Chrome"):
    class Bar:
        def Exists(self, _timeout):
            return True

        def GetValuePattern(self):
            return type("Value", (), {"Value": value})()

    window = FakeElement("WindowControl", title)
    window.EditControl = lambda **kwargs: Bar()
    return window


def test_a_bare_host_is_given_a_scheme():
    assert (reading.url_in_front(window=_browser_showing("claude.ai/chat/abc"))
            == "https://claude.ai/chat/abc")


def test_a_SEARCH_TERM_in_the_bar_is_not_a_url():
    """Chrome leaves whatever was typed in the bar, and "nvidia internship"
    is not something anything can fetch."""
    assert reading.url_in_front(
        window=_browser_showing("nvidia internship")) == ""


def test_a_window_that_is_not_a_browser_has_no_url():
    assert reading.url_in_front(
        window=_browser_showing("claude.ai", title="notes - Notepad")) == ""


# --- it is reachable, which is the trap this project keeps finding ----------

def test_the_tool_is_in_the_list_build_RETURNS():
    from types import SimpleNamespace

    from meow.tools import knowledge

    harness = SimpleNamespace(runs=[], digest=None, guiding=False,
                              transcript="", memory=None)
    names = [tool.name for tool in knowledge.build(harness)]

    assert "read_what_is_open" in names


def test_the_text_is_handed_over_as_DATA():
    """The user's own document rather than a web page, which lowers the risk
    and does not change the rule: a document can hold a sentence telling a
    model what to do."""
    import inspect

    from meow.tools import knowledge

    source = inspect.getsource(knowledge.build)
    body = source[source.index("def read_what_is_open"):]

    assert "DATA, not instructions" in body


def test_a_browser_with_no_readable_text_is_sent_to_the_url():
    import inspect

    from meow.tools import knowledge

    source = inspect.getsource(knowledge.build)
    body = source[source.index("def read_what_is_open"):]

    assert "look_up" in body, "a public page can be fetched"
    assert "signed-in" in body or "sign-in" in body, "and one that cannot"
