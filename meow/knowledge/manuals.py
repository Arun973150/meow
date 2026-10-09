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
    "item", "items", "type", "data",
    # "object" was briefly in this list and it cost more than it saved:
    # `object.duplicate_move` and `nla.duplicate_move` share the label
    # "Duplicate", and the word "object" is the only thing that tells them
    # apart. The noise it carried is dealt with by ranking the CONTEXT
    # instead - see LIKELY_CONTEXTS.
    # Said by every person asking every question, and present in half the
    # descriptions. "show me how to bevel this edge" scored
    # outliner.show_active above mesh.bevel on the strength of "show".
    "teach", "show", "tell", "help", "want", "need", "please", "again",
    "does", "did", "will", "would", "should", "could", "about", "there",
    "here", "then", "when", "something", "thing", "things", "some",
))

# A line has to earn this much to be offered at all. Three is one hit in the
# LABEL, or three anywhere else.
#
# **A WEAK MATCH IS WORSE THAN NO MATCH**, which is the whole reason this
# exists. The block tells the model these lines are authoritative and that
# its own memory is right about half the time - so handing it lines about
# metaballs when it asked about a bouncing ball does not waste tokens, it
# argues against the one source that would have been right. "My render looks
# flat" used to come back with render.view_cancel.
MINIMUM_SCORE = 3.0

# A hit in the LABEL is worth this much more than one anywhere else. "Bevel"
# in the label of mesh.bevel is the thing being asked for; "bevel" in another
# operator's description is a coincidence.
LABEL_WEIGHT = 3.0

# Charged per word of the label the request did NOT ask about, so a line whose
# label is ABOUT the request beats one that merely contains it. Asked "how do
# i move this", "Move" and "Extrude and Move on Normals" both hit once, and
# only one of them is the answer.
WANDERING_PENALTY = 0.5

# What people say against what the application calls it. Hand-written and
# short on purpose: this is the one gap word overlap cannot close, because no
# amount of scoring turns "scale" into "resize".
#
# Blender's own labels are the right-hand side. Measured misses before this
# existed: "how do i scale it" never found transform.resize (labelled
# "Resize"), and "how do i select everything" never found select_all
# (labelled "(De)select All").
#
# Added to the request rather than used to replace words, so a sentence that
# happens to use the application's own vocabulary is unaffected.
# Which keymap contexts a person talking to a desktop companion is plausibly
# in, best first. Scored as a bonus, because the alternative is nonsense: the
# same label exists in the NLA Editor, the Dopesheet, the Grease Pencil
# keymap and half a dozen other editors, and "how do i duplicate this" came
# back with `nla.duplicate_move` on a tie.
#
# Not a filter. A question about the Sequencer is real and its lines are
# still reachable; they just do not win a tie against the 3D viewport, which
# is where somebody saying "this object" is looking.
LIKELY_CONTEXTS = ("Object Mode", "Mesh", "3D View", "Object Non-modal",
                   "Screen", "Window", "Frames", "Sculpt", "Curve",
                   "Armature", "Pose", "Grease Pencil")

# Worth slightly less than a body-word hit, so it only ever breaks a tie and
# never beats a line that actually matches the words better.
CONTEXT_BONUS = 0.8

# Written the way a person would say them. Stemmed into `_SAID_INSTEAD` below,
# once, after `_stem` exists - a constant that calls a function defined later
# in the file is a NameError at import, which is how this was first written.
SAID_INSTEAD = {
    "blender": {
        "scale": ("resize",),
        "size": ("resize",),
        "grab": ("move", "translate"),
        "everything": ("all",),
        "unhide": ("show", "hidden"),
        "spin": ("rotate",),
        "copy": ("duplicate",),
        "subdivide": ("subdivision",),
        "smooth": ("shade", "subdivision"),
    },
}


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


def _stem(word: str) -> str:
    """Trim the endings that stop "rotate" matching "rotating".

    Four characters minimum before trimming, or "ask" becomes "a". Crude on
    purpose: a real stemmer is a dependency and a model call is a GPU, and
    neither is available on the critical path of a spoken turn.

    **The trailing "e" has to go too, and that is not cosmetic.** Trimming
    only the suffix gives "rotating" -> "rotat" and leaves "rotate" alone, so
    the two still do not meet - which is the whole thing this function exists
    to do. Stripping a final "e" afterwards lands both on "rotat", and
    "move"/"moving"/"moves" all on "mov".

    It mangles words that were never inflected - "mode" becomes "mod" - and
    that costs nothing, because BOTH sides of every comparison go through
    here.
    """
    for ending in ("ing", "ed", "es", "s"):
        if len(word) > len(ending) + 3 and word.endswith(ending):
            word = word[: -len(ending)]
            break
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def _words(text: str) -> set:
    """Text as a set of stemmed WORDS - never as a substring haystack.

    This is the fix for the worst bug this file had. Scoring with
    `word in haystack` is substring matching, and on a 2,501 line manual that
    is catastrophic rather than merely loose:

        "bouncing ball"  matched  object.metaball_add      ball in metaball
        "how do i scale" matched  wm.context_scale_float   scale in scale_float
        "edit mode"      matched  object.voxel_size_edit   edit in size_edit

    An operator name is itself several words - `mesh.extrude_region_move` is
    four - so the split has to break on punctuation and underscores too.
    """
    return {_stem(word) for word in re.split(r"[\W_]+", text.lower())
            if len(word) >= MEANINGFUL_LETTERS and word not in FILLER}


def _meaningful(text: str) -> set:
    return _words(text)


# Both sides stemmed, because `wanted` holds stemmed words by the time this
# is consulted and a raw key like "scale" would never match the "scal" in it.
# That was a real bug, hidden by an earlier stemmer that left "scale" alone.
_SAID_INSTEAD = {
    application: {_stem(said): tuple(_stem(w) for w in instead)
                  for said, instead in table.items()}
    for application, table in SAID_INSTEAD.items()
}


@dataclass
class Manual:
    """One application's reference, and which window it belongs to."""

    application: str                 # how a person says it: "blender"
    executable: str                  # how the digest reports it: "blender.exe"
    version: str = ""
    shortcuts: list = field(default_factory=list)

    def about(self, request: str, limit: int = MAX_LINES,
              context: str = "") -> list:
        """The lines worth putting in front of the model, best first.

        Word overlap, model-free - the same technique as recipe retrieval and
        for the same two reasons: this runs on the critical path of a turn,
        and the target machine has no GPU. An embedding hop here would be
        wrong twice over.
        """
        wanted = _words(request)
        if not wanted:
            return []
        # The vocabulary gap, added rather than substituted. See
        # SAID_INSTEAD - and note that BOTH SIDES are stemmed, because
        # `wanted` holds stemmed words and a raw key like "scale" would
        # never match the "scal" in it. That was a real bug hidden by a
        # stemmer that happened to leave "scale" alone.
        spoken = _SAID_INSTEAD.get(self.application.lower(), {})
        for word in list(wanted):
            wanted.update(spoken.get(word, ()))
        # Where they ACTUALLY are, when something can say so - `scene.py`
        # reads it from Blender itself. A shortcut is only correct inside one
        # context, so this is the strongest signal available and it is worth
        # more than the standing preference below.
        here = (context or "").strip()

        scored = []
        for shortcut in self.shortcuts:
            label = _words(shortcut.label)
            body = _words(shortcut.description) | _words(shortcut.operator)
            in_label = wanted & label
            score = (LABEL_WEIGHT * len(in_label)
                     + len(wanted & body)
                     - WANDERING_PENALTY * len(label - wanted))
            if score < MINIMUM_SCORE:
                continue
            if here and shortcut.context == here:
                score += CONTEXT_BONUS * 2
            elif shortcut.context in LIKELY_CONTEXTS:
                # Earlier in the list is more likely, so the bonus tapers.
                place = LIKELY_CONTEXTS.index(shortcut.context)
                score += CONTEXT_BONUS * (1.0 - place / len(LIKELY_CONTEXTS))
            scored.append((score, shortcut))
        # Ties broken by the shorter label, which is the more central
        # operator: "Move" over "Interactive Light Track to Cursor".
        scored.sort(key=lambda row: (-row[0], len(row[1].label), row[1].keys))
        return [shortcut for _score, shortcut in scored[:limit]]

    def to_prompt(self, request: str, limit: int = MAX_LINES,
                  context: str = "") -> str:
        """The block for the prompt. Empty when nothing matched."""
        lines = self.about(request, limit, context)
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
                  limit: int = MAX_LINES, context: str = "") -> str:
        manual = self.for_window(app, title)
        if manual is None:
            return ""
        return manual.to_prompt(request, limit, context)


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
