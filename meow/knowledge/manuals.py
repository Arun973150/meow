"""An application's own reference, read from the copy that is installed.

A skill (`meow/recipes/blender.md`) is 2.7KB of hand-written knowledge and it
is loaded whole, every turn that application is in front. That is the right
shape for "how this program thinks" and the wrong shape for its command
surface: Blender 4.1.1 defines **2,501 keyboard shortcuts across 267 contexts
and 2,297 operators**, which is 609KB. Nothing like that goes in a prompt.

So a manual is a skill that is too big to inject whole, and what goes in is
the handful of lines a lookup found. Same mechanism, same per-application
scoping, different size.

**MEASURED, because the lesson of this project is that obvious improvements
often buy nothing.** Twenty-six shortcut questions, built from Blender's own
keymap so there is no hand-labelling and nothing to argue about, asked of the
same model the harness teaches with:

    from memory        17/26   (65%)
    with the manual    26/26   on this run

One run of 26, and `temperature=0` is not determinism in this project, so
treat the second number as "it stopped being the bottleneck" rather than as
100%.

Two of the nine answers it fixed would have DAMAGED the user's work:

    "how do I delete a keyframe"   it said X        -> deletes the OBJECT
    "how do I mirror this"         it said shift+D  -> DUPLICATES it

The rest are the derailing kind - Clear Scale as Ctrl+A, which is the Apply
menu; Batch Rename as F2, which renames one thing; Set 3D Cursor as
Shift+right click, which is what it was before 2.8 changed it.

⚠ **A METHODOLOGY BUG CAME FIRST AND GAVE 50% -> 85%.** The question set was
keyed on the operator and its properties rather than on what the model is
SHOWN. "Hide Collection" is one label with a binding per collection, so 1
through 9 were nine separate rows and one identical question; the model was
asked to pick one of nine and marked wrong for picking another. "View Orbit"
on four numpad keys, "Select" with and without extend, and "Play Animation"
forwards and in reverse were all the same shape. A question is fair only when
the words the model sees have exactly one answer.

⚠ **I WROTE A CAVEAT HERE SAYING THE WIN NEEDED THE CONTEXT. IT WAS WRONG,
AND MEASURING IT WAS CHEAP.** The reasoning was sound - a shortcut is only
correct inside one context, X deletes the object in Object Mode and opens a
menu in Edit Mode, and nothing in the accessibility tree says which mode
Blender is in. The conclusion did not follow: every retrieved line CARRIES
its own context, so the model picks the right one without being told.

Re-measured on 24 questions with the context never stated, all for the mode
Blender was really in:

    from memory                        11/24   (46%)
    the manual, context NOT stated     24/24
    the manual plus a live read        24/24

So the honest headline is better than the first one, not worse. And reading
Blender's live state - which was built to close this gap - closes nothing,
because there was no gap. That is the fourth measured improvement in this
project to buy exactly zero.

**Trust.** These lines come from the installed application, not from a page
about some other version, which is a better position than `find_how_to` has
ever had. The containment rule still applies to anything fetched; it does not
need to apply here, and that is the point of preferring this first.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# How many lines of reference go into a turn. Six is about 60 tokens and is
# what the measurement used - enough for the right binding to be among them
# and few enough that the model is not reading a manual out loud.
MAX_LINES = 6

# A word has to be this long to count. Shorter ones are "the", "and", "in",
# and every one of them matches everything.
MEANINGFUL_LETTERS = 3

# Words that name no topic. The recipe loader learned this the hard way -
# "add", "use", "set" and "change" let one note match anything - and a manual
# of 2,501 lines is far more exposed to it, because almost every operator
# description contains a generic verb.
FILLER = frozenset((
    "the", "and", "for", "with", "from", "this", "that", "into", "onto",
    "add", "use", "set", "change", "put", "make", "get", "how", "what",
    "where", "which", "can", "you", "your", "its", "all", "any", "new",
    "current", "active", "selected", "selection", "value", "values",
    "object", "objects", "item", "items", "mode", "type", "data",
))


@dataclass(frozen=True)
class Shortcut:
    """One key combination, and the one context it is correct in."""

    keys: str
    operator: str
    context: str
    description: str = ""
    label: str = ""
    properties: dict = field(default_factory=dict, compare=False)

    def describe(self) -> str:
        extra = ""
        if self.properties:
            extra = " (" + ", ".join(f"{k}={v}" for k, v in
                                     sorted(self.properties.items())) + ")"
        words = self.description or self.label
        return (f"{self.keys} = {self.operator}{extra}, in {self.context}"
                + (f" - {words}" if words else ""))

    def haystack(self) -> str:
        return " ".join((self.label, self.description, self.operator,
                         self.keys, self.context)).lower()


def _meaningful(text: str) -> set:
    return {word for word in re.split(r"\W+", text.lower())
            if len(word) >= MEANINGFUL_LETTERS and word not in FILLER}


@dataclass
class Manual:
    """One application's reference, and which window it belongs to."""

    application: str                 # how a person says it: "blender"
    executable: str                  # how the digest reports it: "blender.exe"
    version: str = ""
    shortcuts: list = field(default_factory=list)

    def about(self, request: str, limit: int = MAX_LINES) -> list:
        """The lines worth putting in front of the model, best first.

        Word overlap, model-free - the same technique as recipe retrieval and
        for the same two reasons: this runs on the critical path of a turn,
        and the target machine has no GPU. An embedding hop here would be
        wrong twice over.
        """
        wanted = _meaningful(request)
        if not wanted:
            return []
        scored = []
        for shortcut in self.shortcuts:
            haystack = shortcut.haystack()
            hits = sum(1 for word in wanted if word in haystack)
            if not hits:
                continue
            # A hit in the LABEL counts double. "Bevel" in the label of
            # mesh.bevel is the thing being asked for; "bevel" appearing in
            # some other operator's description is a coincidence.
            label = shortcut.label.lower()
            hits += sum(1 for word in wanted if word in label)
            scored.append((hits, shortcut))
        scored.sort(key=lambda row: (-row[0], row[1].keys))
        return [shortcut for _hits, shortcut in scored[:limit]]

    def to_prompt(self, request: str, limit: int = MAX_LINES) -> str:
        """The block for the prompt. Empty when nothing matched."""
        lines = self.about(request, limit)
        if not lines:
            return ""
        return (
            f"From the keymap of the {self.application} "
            f"{self.version} INSTALLED ON THIS MACHINE. These are exact for "
            f"this version - prefer them over what you remember, which is "
            f"measured as right about half the time:\n"
            + "\n".join(f"  {line.describe()}" for line in lines)
            + "\n  A shortcut is only correct in its own context. If you do "
              "not know which editor or mode they are in, say the context "
              "with the key rather than guessing one."
        )


@dataclass
class Library:
    """Every manual that loaded, and every file that did not."""

    manuals: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    def for_window(self, app: str, title: str = "") -> Manual | None:
        """The manual for the window in front, or None.

        Matched on the EXECUTABLE first, because that is what the digest
        reports and it cannot be confused: a Chrome tab whose title says
        "Blender tutorial" is not Blender, and matching on the title would
        hand Blender's keymap to a web page. The title is only consulted when
        the executable did not match and the manual names no executable.
        """
        name = (app or "").lower().strip()
        for manual in self.manuals:
            if manual.executable and manual.executable.lower() == name:
                return manual
        for manual in self.manuals:
            if manual.executable:
                continue
            spoken = manual.application.lower()
            if spoken and spoken in (title or "").lower():
                return manual
        return None

    def to_prompt(self, request: str, app: str = "", title: str = "",
                  limit: int = MAX_LINES) -> str:
        manual = self.for_window(app, title)
        if manual is None:
            return ""
        return manual.to_prompt(request, limit)


def folder() -> Path:
    """Where the manuals live. App data, not Documents.

    Generated rather than hand-written, measured in hundreds of kilobytes, and
    rebuilt when the application is upgraded - none of which is a thing the
    user opens, so it is not their folder. And nothing here is a database, so
    the sync argument does not apply; it is kept beside the databases because
    it is the app's own state.
    """
    from ..storage import app_data

    place = app_data() / "manuals"
    place.mkdir(parents=True, exist_ok=True)
    return place


def _shortcuts_from(bundle) -> list:
    """Read the JSON a generator wrote, defensively.

    Parsed loosely on purpose. A manual is produced by a script that runs
    inside another application, across versions nobody here controls, and a
    reference that loses one malformed row is worth far more than one that
    refuses to load because a field moved.
    """
    descriptions = {}
    for row in bundle.get("operators") or []:
        try:
            descriptions[row["operator"]] = (row.get("label") or "",
                                             row.get("description") or "")
        except Exception:  # noqa: BLE001
            continue

    shortcuts = []
    for row in bundle.get("shortcuts") or []:
        try:
            operator = row["operator"]
            label, description = descriptions.get(operator, ("", ""))
            shortcuts.append(Shortcut(
                keys=str(row["keys"]),
                operator=str(operator),
                context=str(row.get("context") or ""),
                label=label,
                description=description,
                properties={str(k): str(v) for k, v
                            in (row.get("properties") or {}).items()},
            ))
        except Exception:  # noqa: BLE001
            continue
    return shortcuts


def load(where: Path | None = None) -> Library:
    """Read every manual on disk. Never raises."""
    place = where if where is not None else folder()
    library = Library()
    try:
        paths = sorted(place.glob("*.json"))
    except Exception as error:  # noqa: BLE001
        library.skipped.append((str(place), f"{type(error).__name__}: {error}"))
        return library

    for path in paths:
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:  # noqa: BLE001
            library.skipped.append((path.name,
                                    f"{type(error).__name__}: {error}"))
            continue
        shortcuts = _shortcuts_from(bundle)
        if not shortcuts:
            library.skipped.append((path.name, "no usable shortcuts in it"))
            continue
        library.manuals.append(Manual(
            application=str(bundle.get("application") or path.stem),
            executable=str(bundle.get("executable") or ""),
            version=str(bundle.get("version") or ""),
            shortcuts=shortcuts,
        ))
    return library
