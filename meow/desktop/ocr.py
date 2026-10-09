"""Reading the words on screen, with the engine Windows already ships.

**MEASURED TWICE, and it is a reader rather than a pointer. Those are
different jobs and only one of them works.**

AS GROUNDING, IT FAILS. Scored on the same 44 hand-labelled targets as
everything else (`meow evaluate --labelled ocr`):

    answered 14/44, hit 4, precision 29%     against computer-use 21-25/44

And no threshold rescues it, because **the HIGHEST scoring answer is a
miss**: "blade edit mode" matched the text "Blade Edit Mode" exactly, scored
7.0, and pointed somewhere the user did not mean. The text that NAMES a thing
is not the thing - a label beside a control, a menu entry duplicating a
toolbar button, a tooltip. All read perfectly, all point wrongly. It also
cannot see an icon at all: a razor tool, a move tool and a chess piece have
no text, which is most of the 44.

AS A READER OF A WINDOW THE TREE CANNOT SEE, IT IS THE ONLY THING THERE IS.
Measured on a live Blender:

    the accessibility tree    5 elements - Minimize, Maximize, Close, System
    OCR                       128 words, 65 lines, 801ms

and those lines are the interface: the File/Edit/Render menus, the Layout,
Modeling, Sculpting, UV Editing and Shading workspace tabs, the outliner's
Collection / Camera / Light / Cube, the Transform panel with Rotation X and
Scale X and their values - and **"Object Mode"**, which is the one fact that
otherwise needs Blender's own add-on and a socket.

For comparison, `look_at_screen` on one window measured 5,947ms for
gpt-4o-mini and 14,584ms for gpt-5-mini. This is 801ms and exact about the
words, where a vision model paraphrases them.

⚠ **It garbles small text and splits words.** From that same reading:
"Object Mod e", "Vew", "Sha dima", "Collectbns", "Sce e", and out of VS Code
"Traceback (nnst recent call last)" and "AGENTSmd". So it is evidence about
what is on screen, never a quotation - anything that needs the exact string
has to come from the tree, which is exact where it can see at all.

**Windows.Media.Ocr, not Tesseract or EasyOCR or Paddle.** Already installed -
this machine reports en-GB and en-US - so no model download, no `torch`, no
GPU, no new dependency. The target is CPU-only and this project has already
refused one SDK over a version conflict; an OCR stack that drags in `torch`
to read a menu bar is the same mistake with more disk.

**Through PowerShell, which costs the process startup and is still the right
trade.** WinRT's async methods need awaiting and Python cannot without
`winsdk`. 800ms of that is mostly PowerShell starting, and it is measured
against 6,000-14,000ms for the alternative.

⚠ **The result comes back in a FILE, as UTF-8.** Windows PowerShell 5.1
encodes stdout in the ANSI codepage, and an application's own UI text is
routinely not representable in cp1252 - a reader thread died on byte 0x90 out
of Photoshop and the whole reading was lost SILENTLY, which quietly
understated the first measurement.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Where the PowerShell that drives the WinRT engine lives. Shipped as a file
# rather than passed with -Command: it needs reflection over
# System.WindowsRuntimeSystemExtensions to await an IAsyncOperation, and that
# does not survive being flattened onto a command line.
SCRIPT = Path(__file__).with_name("winocr.ps1")

# Generous. Measured at 924ms end to end for a 1280x800 screenshot, almost
# all of it PowerShell starting, and a 4K screen is bigger.
TIMEOUT_SECONDS = 20.0

# A word has to be this long to count towards a match. Shorter ones are "a",
# "of", "to" and every one of them appears somewhere on every screen.
MEANINGFUL_LETTERS = 3

# Words that name nothing. Shorter than the manual's list on purpose: this
# matches against what is literally on screen rather than against prose, so
# "the" and "this" are the whole problem and "active" is not.
FILLER = frozenset((
    "the", "and", "for", "with", "from", "this", "that", "into", "onto",
    "how", "what", "where", "which", "can", "you", "your", "its",
    "show", "tell", "teach", "find", "please", "button", "icon", "option",
))


@dataclass(frozen=True)
class Word:
    """One word Windows read, and where it was, in IMAGE pixels."""

    text: str
    left: int
    top: int
    right: int
    bottom: int
    # The whole line it came from. Kept because a target is usually named by
    # several words - "colour mode", "new project" - and the line is how they
    # are known to belong together.
    line: str = ""

    @property
    def centre(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


@dataclass
class Reading:
    """Everything readable in one image."""

    words: list = None
    width: int = 0
    height: int = 0
    error: str = ""

    def __post_init__(self):
        if self.words is None:
            self.words = []

    def __bool__(self) -> bool:
        return bool(self.words)


def available() -> bool:
    """Is there an OCR engine for this machine's languages?

    Asked of Windows rather than assumed: the engine ships with the language
    packs, so a machine with no English pack has the API and no recognizer,
    and `TryCreateFromUserProfileLanguages` returns null rather than raising.
    """
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Windows.Media.Ocr.OcrEngine,Windows.Foundation,"
             "ContentType=WindowsRuntime] | Out-Null; "
             "[Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages.Count"],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001
        return False
    try:
        return int((done.stdout or "0").strip().splitlines()[0]) > 0
    except Exception:  # noqa: BLE001
        return False


def read(image) -> Reading:
    """Every word in a PIL image, with boxes. Never raises.

    The image is written to a temporary PNG because the WinRT decoder takes a
    file, and PNG because this is text: JPEG ringing round a 9px glyph is
    exactly the artefact an OCR engine cannot see past.
    """
    if not SCRIPT.is_file():
        return Reading(error=f"missing {SCRIPT.name}")

    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    where = Path(handle.name)
    handle.close()
    answer = where.with_suffix(".json")
    try:
        try:
            image.convert("RGB").save(where, format="PNG")
        except Exception as error:  # noqa: BLE001
            return Reading(error=f"{type(error).__name__}: {error}")

        try:
            # The result comes back in a FILE, and stderr is decoded with
            # errors="replace". Windows PowerShell 5.1 writes the console in
            # the ANSI codepage, so an application's own UI text can be
            # undecodable - a reader thread died on byte 0x90 out of
            # Photoshop and the whole reading was silently lost.
            done = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(SCRIPT), "-Path", str(where),
                 "-Out", str(answer)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=TIMEOUT_SECONDS)
        except Exception as error:  # noqa: BLE001
            return Reading(error=f"{type(error).__name__}: {error}")

        if done.returncode != 0 or not answer.is_file():
            trouble = (done.stderr or "").strip().splitlines()
            return Reading(error=trouble[0][:160] if trouble else "powershell failed")
        try:
            payload = json.loads(answer.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return Reading(error=f"unreadable output: {error}")

        words = []
        for row in payload.get("words") or []:
            try:
                words.append(Word(
                    text=str(row["text"]), left=int(row["left"]),
                    top=int(row["top"]), right=int(row["right"]),
                    bottom=int(row["bottom"]), line=str(row.get("line") or "")))
            except Exception:  # noqa: BLE001 - one bad row is not a failure
                continue
        return Reading(words=words, width=int(payload.get("width") or 0),
                       height=int(payload.get("height") or 0))
    finally:
        for leftover in (where, answer):
            try:
                leftover.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass


def _meaningful(text: str) -> set:
    return {word for word in re.split(r"[\W_]+", text.lower())
            if len(word) >= MEANINGFUL_LETTERS and word not in FILLER}


def find(description: str, reading: Reading, limit: int = 4) -> list:
    """Where the words of a description are, best first.

    Scored on a RUN OF ADJACENT WORDS, not on the whole line. A line is a
    visual row, and a menu bar is one row holding nine unrelated items - so
    penalising a line for being long, which the first version did, scored
    Photoshop's real `Filter` menu at 0.25 and a "Filter Filter Recent Files"
    panel at 1.50. The length of the row a word sits in says nothing about
    the word.

    A target is usually named by several adjacent words - "colour mode", "new
    project", "effect controls" - so the run is the unit that can match all
    of them, and the box returned is the run's own extent rather than the
    row's.
    """
    wanted = _meaningful(description)
    if not wanted or not reading:
        return []

    by_line: dict = {}
    for word in reading.words:
        by_line.setdefault(word.line or word.text, []).append(word)

    scored = []
    for line, words in by_line.items():
        words = sorted(words, key=lambda word: word.left)
        hits = [bool(_meaningful(word.text) & wanted) for word in words]
        start = 0
        while start < len(words):
            if not hits[start]:
                start += 1
                continue
            stop = start
            while stop + 1 < len(words) and hits[stop + 1]:
                stop += 1
            run = words[start:stop + 1]
            covered = set()
            for word in run:
                covered |= _meaningful(word.text) & wanted
            # Two per word of the request this run accounts for, and a bonus
            # for accounting for ALL of it: "Colour Mode" beats a stray
            # "Mode" even though both are adjacent runs.
            score = len(covered) * 2.0
            if covered == wanted:
                score += 1.0
            box = (min(w.left for w in run), min(w.top for w in run),
                   max(w.right for w in run), max(w.bottom for w in run))
            scored.append((score, box, " ".join(w.text for w in run)))
            start = stop + 1

    # Ties go to the SMALLER box: a tight run round the words asked for is a
    # better answer than a wide one that happens to contain them.
    scored.sort(key=lambda row: (-row[0],
                                 (row[1][2] - row[1][0]) * (row[1][3] - row[1][1])))
    return scored[:limit]


# How many lines of a reading go into a turn. A Blender window is 65 and a
# busy VS Code is 98, and the whole point is that this is cheap - 65 lines is
# about 200 tokens against 2,833 for the low-detail picture of the same
# window. Capped anyway, because a text editor full of prose is not an
# interface description.
MAX_LINES = 70

# Lines shorter than this are read noise. The engine emits stray marks as
# one-character "words" - "o", "I", "v" - and on a dense interface there are
# dozens.
MEANINGFUL_LINE = 3


def lines_of(reading: Reading, limit: int = MAX_LINES) -> list:
    """The distinct lines, in reading order, deduplicated.

    Lines rather than words: a word on its own has lost the thing that gave
    it meaning. "Scale" is a panel heading, a transform tool and a property,
    and "Scale X 1.000" is a value.
    """
    seen, out = set(), []
    for word in reading.words:
        line = (word.line or word.text).strip()
        if len(line) < MEANINGFUL_LINE or line in seen:
            continue
        seen.add(line)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def describe(reading: Reading, app: str = "") -> str:
    """The block for the prompt, or "" when there was nothing to read.

    Written to be used as EVIDENCE rather than quoted. The engine garbles
    small text - "Object Mod e", "Collectbns", "Traceback (nnst recent call
    last)" - so a model that repeats a line verbatim will say something that
    is not on screen, and a model that treats it as a wrong answer will
    ignore the one description of a window nothing else can see.
    """
    lines = lines_of(reading)
    if not lines:
        return ""
    where = f" in {app}" if app else ""
    return (
        f"THE WORDS ON THEIR SCREEN{where}, read by the operating system's own "
        f"OCR. The accessibility tree cannot see inside this window, so this "
        f"is the only description of it there is:\n"
        + "\n".join(f"  {line}" for line in lines)
        + "\n\nUse it as EVIDENCE, not as a quotation. Small text comes back "
          "garbled and words get split - 'Object Mod e' is \"Object Mode\" - "
          "so read through the damage, and never read one of these lines out "
          "as if it were the label. If it tells you which mode or tab or "
          "panel they are in, say so and act on it rather than asking."
    )
