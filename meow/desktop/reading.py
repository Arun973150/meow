"""The text of whatever is open, including the part scrolled out of view.

"Summarise what i am reading" saw the visible part only - a screenshot, or
the control list, neither of which holds the paragraph below the fold. UIA's
`TextPattern` holds the whole thing, exactly, for nothing.

**MEASURED, on the windows that were actually open on this machine:**

```
VS Code, GetText(-1)         439,329 chars, 8,957 lines   383ms
VS Code, GetText(12000)       12,000 chars                 15ms
VS Code, GetVisibleRanges()   12,000 chars             65,387ms  <- unusable
VS Code, through read()       11,742 chars, cut           179ms
a Claude chat page             9,587 chars                927ms
a Google Form                  1,993 chars                335ms
Chrome's New Tab                 783 chars                757ms
the address bar               exact                          1ms
```

⚠ **THE FIRST MEASUREMENT SAID A WEB PAGE WAS NOT READABLE AT ALL,
AND THAT WAS MY OWN SEARCH BUG.** It reported 8 candidates in Chrome whose
best was 17 characters - "Create split view", a toolbar tooltip - and the
conclusion drawn was that Chrome does not project page text into
`TextPattern`. A tidy story, which would even have explained why HeyClicky
ships a Chrome extension. The real cause: `GetTextPattern` is declared per
control CLASS, an `EditControl` has it, and the first match found was
**"Address and search bar"** - which returns the URL and looks exactly like
a 51-character document. Rank the candidates by role and size, exclude the
address bar by name, and a page hands over thousands of characters of its
real content.

Worth remembering as a habit: a negative result about a PLATFORM is the one
most worth re-checking, because it closes a door for months.

The findings that held:

- **A bounded `GetText` is the whole mechanism.** `GetText(12000)` is 15ms
  where `GetText(-1)` is 383ms, because the provider never materialises the
  other 427,000 characters. The cap is not a courtesy applied after reading
  - it is the reason this is affordable at all.
- **`GetVisibleRanges` is sixty-five SECONDS on a flattened Electron
  window.** It was going to be the cheap exact answer to "what am I looking
  at", and it is not available at any price in a voice turn.
- **What comes back is the WINDOW flattened**, not the document: the VS Code
  read begins "File Edit Selection View Go Run Terminal Help" and a Claude
  page begins with its sidebar. Real content with the chrome still attached,
  which is why `_tidy` exists and why the reply says what this is.
- **The URL is there too**, and it is the only route left when the text is
  not enough: the research fetcher can read a public page. A signed-in page
  is reachable by neither, and saying so is the honest answer.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

# What a turn can afford, and the reason the read is fast: this number is
# passed to `GetText`, so the provider stops there. A screenshot is 2,833
# tokens flat, and this is deliberately the same order - about 3,000 tokens
# of exact text instead of 2,833 tokens of pixels.
MOST_CHARACTERS = 12_000

# Hunting for the document is the part that can wander: Electron nests ~30
# levels and a wrong turn walks a whole pane. The READ itself is bounded by
# MOST_CHARACTERS rather than by time.
HUNT_SECONDS = 1.5

# A document is a container near the top of its own subtree. This is not the
# digest's 50: that one has to reach the deepest actionable control, and
# this one only has to reach the editor.
MOST_DEPTH = 12

# Ranked, best first. A DocumentControl is the thing; a TextControl is
# usually a label; an EditControl might be the document in Notepad and is
# the ADDRESS BAR in Chrome, which is why it is last and why the names
# below are excluded outright.
PREFERRED_ROLES = ("DocumentControl", "EditControl", "TextControl")

# Never these, whatever they contain. The address bar supports TextPattern,
# holds a plausible-looking 51 characters, and is not what anybody means by
# "what i am reading".
NOT_A_DOCUMENT = ("address and search bar", "address bar", "search",
                  "find", "omnibox", "url")

# UIA pads a flattened window with these: U+FFFC where an image or a
# non-text child sat, and private-use glyphs for icon fonts. They are most
# of the noise in an Electron read and they carry nothing but tokens.
NOISE = re.compile(r"[￼​-]+")


@dataclass(frozen=True)
class Reading:
    """Text read off a window, and what kind of answer it is."""

    text: str
    kind: str          # "whole" | "selection"
    cut: bool          # there is more than MOST_CHARACTERS of it
    source: str        # the control it came from, for the reply
    seconds: float = 0.0

    @property
    def found_anything(self) -> bool:
        return bool(self.text.strip())

    def describe(self) -> str:
        """One line for the model, honest about what it is NOT."""
        if not self.found_anything:
            return ""
        what = ("what the user has SELECTED" if self.kind == "selection"
                else "the text of the document they have open, INCLUDING the "
                     "part scrolled out of view")
        said = f"{what}, read exactly from the window rather than from pixels"
        if self.cut:
            said += (f" - CUT at {len(self.text):,} characters, so there is "
                     f"more after this and you must not say it is all of it")
        return said


def _tidy(text: str) -> str:
    """Drop the padding UIA adds, keep the words.

    Not cosmetic: on a flattened Electron window the placeholders and icon
    glyphs are a real share of the characters, and every one is a token that
    says nothing.
    """
    text = NOISE.sub(" ", text or "")
    # Flattened windows arrive one word per line - "File\nEdit\nSelection" -
    # which multiplies the newlines without adding structure. Runs of blank
    # lines collapse; single newlines stay, because in a real document they
    # are the paragraphs.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]*(?:\n[ \t]*)+", "\n\n", text)
    return text.strip()


def _rank(element) -> tuple:
    """How good a document candidate this is. Bigger sorts first."""
    try:
        role = str(element.ControlTypeName or "")
        name = str(element.Name or "")
        rect = element.BoundingRectangle
        area = max(0, rect.width()) * max(0, rect.height())
    except Exception:  # noqa: BLE001
        return (-1, 0)
    if any(word in name.lower() for word in NOT_A_DOCUMENT):
        return (-1, 0)
    try:
        preference = len(PREFERRED_ROLES) - PREFERRED_ROLES.index(role)
    except ValueError:
        preference = 0
    return (preference, area)


def _candidates(window, deadline: float) -> list:
    """Every descendant with a text pattern, with the best first."""
    found, waiting = [], [(window, 0)]
    while waiting and time.monotonic() < deadline:
        element, depth = waiting.pop()
        if depth > MOST_DEPTH:
            continue
        try:
            children = element.GetChildren()
        except Exception:  # noqa: BLE001
            continue
        for child in children:
            try:
                pattern = child.GetTextPattern()
            except Exception:  # noqa: BLE001 - the getter is per control class
                pattern = None
            if pattern is not None:
                found.append((child, pattern))
            else:
                waiting.append((child, depth + 1))
    found.sort(key=lambda pair: _rank(pair[0]), reverse=True)
    return [pair for pair in found if _rank(pair[0])[0] >= 0]


def read(kind: str = "whole", window=None) -> Reading | None:
    """Read the window in front. None when there is no text to read.

    None rather than an empty Reading, so the caller can tell "this window
    holds no readable text" - a browser, a canvas, a game - from "the
    document is empty". That distinction is the whole answer for a browser,
    where the honest reply names the URL instead.
    """
    deadline = time.monotonic() + HUNT_SECONDS
    began = time.perf_counter()
    try:
        import uiautomation

        front = (window if window is not None
                 else uiautomation.GetForegroundControl())
    except Exception:  # noqa: BLE001
        return None
    if front is None:
        return None

    for element, pattern in _candidates(front, deadline):
        # ONE MORE than the cap, which is how "there is more" is known
        # without the 383ms it costs to read all of it.
        wanted = MOST_CHARACTERS + 1
        try:
            if kind == "selection":
                raw = _joined(_ranges(pattern.GetSelection), wanted)
            else:
                raw = pattern.DocumentRange.GetText(wanted) or ""
        except Exception:  # noqa: BLE001 - try the next candidate
            continue

        text = _tidy(raw)
        if not text:
            continue
        cut = len(raw) > MOST_CHARACTERS
        try:
            name = str(element.Name or "") or str(element.ControlTypeName or "")
        except Exception:  # noqa: BLE001
            name = ""
        return Reading(text=text[:MOST_CHARACTERS], kind=kind, cut=cut,
                       source=name, seconds=time.perf_counter() - began)
    return None


def _ranges(getter) -> list:
    """The ranges a TextPattern getter hands back, defensively."""
    try:
        got = getter()
    except Exception:  # noqa: BLE001
        return []
    if got is None:
        return []
    try:
        return list(got)
    except TypeError:
        return [got]


def _joined(ranges, wanted: int) -> str:
    pieces = []
    for one in ranges:
        try:
            pieces.append(one.GetText(wanted) or "")
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(piece for piece in pieces if piece)


def url_in_front(window=None) -> str:
    """The address bar's value, or "".

    The one thing a browser DOES hand over, and the only route to a web
    page's content: the research fetcher can read a public URL. A signed-in
    page is reachable by neither this nor the page text, and the honest
    answer is to say so rather than to describe the screenshot.
    """
    try:
        import uiautomation

        front = (window if window is not None
                 else uiautomation.GetForegroundControl())
        if front is None:
            return ""
        title = str(front.Name or "")
        if not any(mark in title for mark in
                   ("Google Chrome", "Edge", "Firefox", "Brave", "Opera")):
            return ""
        bar = front.EditControl(searchDepth=MOST_DEPTH)
        if not bar.Exists(0.4):
            return ""
        value = str(bar.GetValuePattern().Value or "").strip()
    except Exception:  # noqa: BLE001
        return ""
    if not value or " " in value:
        # A search term rather than an address. Chrome leaves whatever was
        # typed in the bar, and "nvidia internship" is not fetchable.
        return ""
    # Chrome hides the scheme, and a bare host is not something to fetch.
    if not value.startswith(("http://", "https://", "file://")):
        value = "https://" + value
    return value
