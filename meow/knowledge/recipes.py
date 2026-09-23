"""Phase 2.6 - recipes. A new capability is a file, not a release.

From `docs/01-architecture.md`:

> A new capability is a **retrieved paragraph**, not a release.
> RAG over a recipe folder. The capability set is open-ended by construction.

That is invariant 1 made concrete. When the cat cannot do something, the fix is
supposed to be a paragraph explaining how - not another agent, and usually not
another tool. This is the mechanism that makes that true.

**A recipe is knowledge, not a script.** It does not encode steps to replay; it
tells the model what it did not know. "The New Slide button is on the Insert
ribbon, and the title placeholder is called Title 1" is worth more than a
recorded sequence, because it survives the window being a different size, the
ribbon being collapsed, and the user being on a different slide.

**Retrieval is model-free.** The target machine is CPU-only and this runs
before every act turn, so an embedding model - local or hosted - is the wrong
shape twice over: latency on the path that can least afford it, and a
dependency that has to be installed before the cat can open a window. Word
overlap against the trigger line does the job, and a recipe folder is tens of
files rather than millions of documents.

**Recipes are trusted, and that is a real distinction.** They are local files
the user wrote, so unlike a web page in `meow/lookup.py` they are allowed to
contain instructions - that is the entire point of them. The line to hold is
that nothing writes into this folder except a person. If a recipe could be
authored by fetched content, the containment argument in `lookup.py` would be
worth nothing, because the long way round would be open.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# Two at most. A recipe is injected into the prompt of every matching act turn,
# so this is a recurring cost per turn rather than a one-off, and three
# paragraphs of half-relevant guidance is worse than one of the right one.
MAX_RECIPES = 2

# Below this a match is coincidence. Measured against the shipped recipes:
# unrelated requests score 0.0 to 0.17, real matches 0.33 and up.
MINIMUM_SCORE = 0.30

# A word in the trigger line counts fully; a word found only in the body counts
# half. The body is knowledge rather than an index - "dark mode" appears in the
# Settings recipe as the thing being explained, not as a keyword - so it is
# real evidence of relevance, just weaker than a term the author chose to list.
BODY_WEIGHT = 0.5

# Words that appear in every request and so distinguish nothing.
FILLER = {
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or", "my",
    "me", "i", "you", "it", "this", "that", "is", "are", "can", "could",
    "would", "please", "do", "does", "how", "what", "where", "with", "from",
    "up", "out", "about", "some", "any", "want", "need", "get", "make",
    "these", "those", "them", "there", "here", "now", "then", "also", "just",
    # Words that name no topic. They matter because the TITLE counts as a
    # trigger, and a title written as a sentence - "start something new in an
    # application" - donates "start" and "something" to the trigger set, where
    # they match anything. "start recording" and "film something" both scored
    # a tie against the new-document recipe on exactly those two words.
    "start", "something", "thing", "things", "stuff", "someone", "somewhere",
}

TRIGGER_LINE = re.compile(r"^\s*when\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

# An optional route through a UI, written by hand:
#
#     route: dark mode = Personalization > Colors
#
# The left side is what somebody would ask for, the right is the actual
# sequence of controls. This exists because mining a route off a web page
# is unreliable in both directions: "how do i change dark mode" mined
# "Theme, Display" - plausible and not the real path - and "how do i change
# my dns" mined nothing at all. A route somebody wrote down is local, free,
# trusted and correct, which is four things a fetched page is not.
ROUTE_LINE = re.compile(r"^\s*route\s*:\s*(.+?)\s*=\s*(.+)$",
                        re.IGNORECASE | re.MULTILINE)


def shipped_folder() -> Path:
    """The recipes that come with Meow. INSIDE the package, deliberately.

    They sat at the repository root until this file moved one directory
    deeper, at which point `parent.parent` stopped being the root and every
    shipped recipe vanished - silently, because a shelf with nothing on it
    looks exactly like a request that matched nothing.

    Inside the package is also the only place they can be: a wheel contains
    the package, so anything beside it is not installed at all. The path here
    and the `package-data` entry in pyproject.toml have to agree.
    """
    return Path(__file__).resolve().parent.parent / "recipes"


def recipe_folder() -> Path:
    """Where recipes live. Created on first use, next to everything else.

    Beside the documents rather than inside the package, because a recipe is
    the user's, not ours - it has to survive reinstalling Meow, and it has to
    be somewhere a person can actually find and edit.
    """
    from ..storage import recipes_folder

    return recipes_folder()


def _meaningful(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(text).lower())
    return {word for word in words if len(word) > 2 and word not in FILLER}


@dataclass(frozen=True)
class Recipe:
    """One paragraph of knowledge, and what it is about."""

    title: str
    triggers: str
    body: str
    path: str = ""
    # {what someone asks for: [control, control, ...]}
    routes: dict = field(default_factory=dict)

    @property
    def body_terms(self) -> set[str]:
        return _meaningful(self.body)

    @property
    def phrases(self) -> list[set[str]]:
        """Each comma-separated trigger, as the set of words it needs.

        A multi-word trigger is a PHRASE and matches only when all of its
        words are present. Split into loose words instead, "new window" made
        the recipe fire on "minimise this window" - because window alone was
        enough, and window alone means nothing.
        """
        found = []
        for piece in str(self.triggers).split(","):
            words = _meaningful(piece)
            if words:
                found.append(words)
        return found

    @property
    def terms(self) -> set[str]:
        """Every SINGLE-word trigger, plus the title.

        The title counts as a trigger too - people name recipes after the
        thing they are for, so repeating those words on the when: line is
        busywork nobody will keep doing.
        """
        single = set()
        for words in self.phrases:
            if len(words) == 1:
                single |= words
        return single | _meaningful(self.title)

    def score(self, request: str) -> float:
        """How well this recipe fits, from 0 to 1.

        Scored against the REQUEST's words, not the recipe's: a long recipe
        with many terms should not beat a short exact one just for having
        more surface. "open notepad" matching two of its two meaningful words
        is a better fit than matching two of twenty.

        A recipe must match on a TRIGGER word to be considered at all; body
        words then raise the score but cannot produce one on their own.
        Triggers alone were too brittle: "where is the dark mode setting"
        matched only on "setting" and scored 0.33, missing by a hundredth,
        while the recipe body says in so many words that dark mode lives
        under Personalization.
        """
        wanted = _meaningful(request)
        if not wanted:
            return 0.0
        listed = wanted & self.terms
        # A phrase counts when the request contains ALL of its words. That is
        # what makes "new window" mean new window rather than window.
        for phrase in self.phrases:
            if len(phrase) > 1 and phrase <= wanted:
                listed = listed | phrase
        if not listed:
            # Body words BOOST a match; they never create one. Without this
            # rule "what time is it" scored 0.50 against the Settings recipe,
            # on the strength of "Time & language" appearing in a list of
            # sidebar entries - a single-word request needs only one
            # coincidence in a long paragraph to look like a match.
            return 0.0
        # Counted once. A word in both the triggers and the body is one word.
        in_body_only = (wanted & self.body_terms) - listed
        return (len(listed) + BODY_WEIGHT * len(in_body_only)) / len(wanted)

    def to_prompt(self) -> str:
        return f"How to {self.title}:\n{self.body.strip()}"


def parse(text: str, path: str = "") -> Recipe | None:
    """Read one recipe file.

    The format is deliberately barely a format - a heading, a when: line, and
    prose - because the whole promise is that adding a capability means
    writing a paragraph. Anything needing a schema in mind is a format people
    stop using.

        # Add a slide in PowerPoint
        when: powerpoint, slide, new slide, presentation

        Insert ribbon, then New Slide...
    """
    lines = text.splitlines()
    title = ""
    for line in lines:
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            break

    # A when: list WRAPS, and it has to, because a good trigger list is longer
    # than one line. Reading only the matched line silently threw the rest
    # away: new-document.md lost "new file", "new note", "new sheet", "new
    # workbook", "new slide" and "new deck" from the day it was written, and a
    # half-loaded recipe looks exactly like a request that matched nothing.
    #
    # The list runs to the first BLANK line - the rule a person would guess
    # from looking at the file, which is the only rule worth having in a
    # format whose promise is that you can write one without reading any
    # documentation.
    triggers = ""
    trigger_line_numbers: set[int] = set()
    for index, line in enumerate(lines):
        matched = TRIGGER_LINE.match(line)
        if not matched:
            continue
        collected = [matched.group(1).strip()]
        trigger_line_numbers.add(index)
        for offset in range(index + 1, len(lines)):
            following = lines[offset]
            if not following.strip() or following.startswith("#"):
                break
            collected.append(following.strip())
            trigger_line_numbers.add(offset)
        triggers = " ".join(collected).strip()
        break

    # Hand-written routes. Kept out of the body as well, so a route line does
    # not also read as prose when the recipe is put in front of the model.
    routes: dict[str, list[str]] = {}
    route_line_numbers: set[int] = set()
    for index, line in enumerate(lines):
        matched = ROUTE_LINE.match(line)
        if not matched:
            continue
        asked = " ".join(matched.group(1).lower().split())
        steps = [step.strip() for step in
                 re.split(r">|→|,", matched.group(2)) if step.strip()]
        if asked and len(steps) > 1:
            routes[asked] = steps
        route_line_numbers.add(index)

    body_lines = [line for index, line in enumerate(lines)
                  if not line.startswith("#")
                  and index not in trigger_line_numbers
                  and index not in route_line_numbers]
    body = "\n".join(body_lines).strip()

    if not title or not body:
        # A file with no heading or no body is half-written, not a recipe.
        # Skipped rather than guessed at, and the caller reports it.
        return None
    return Recipe(title=title, triggers=triggers, body=body, path=path,
                  routes=routes)


@dataclass
class Shelf:
    """Every recipe that loaded, and every file that did not."""

    recipes: list[Recipe] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def find(self, request: str, limit: int = MAX_RECIPES) -> list[Recipe]:
        """The best recipes for this request, or nothing."""
        scored = [(recipe.score(request), recipe) for recipe in self.recipes]
        good = [(score, recipe) for score, recipe in scored
                if score >= MINIMUM_SCORE]
        good.sort(key=lambda row: row[0], reverse=True)
        return [recipe for _, recipe in good[:limit]]

    def route_for(self, request: str) -> list[str]:
        """A hand-written route matching this request, best first, or [].

        Checked BEFORE the web. A route somebody wrote down is local, free,
        trusted and correct; a route mined from a fetched page is none of
        those reliably - "how do i change dark mode" mined "Theme, Display",
        which is plausible and is not the path, and "how do i change my dns"
        mined nothing at all.

        Matched on the words of the route's own key, so "how do i change dark
        mode to light mode" finds `dark mode`. The longest matching key wins:
        a recipe can hold both `dns` and `dns server`, and the more specific
        one is the one somebody bothered to write.
        """
        wanted = _meaningful(request)
        if not wanted:
            return []
        best: list[str] = []
        best_words = 0
        for recipe in self.recipes:
            for asked, steps in recipe.routes.items():
                key_words = _meaningful(asked)
                if key_words and key_words <= wanted and len(key_words) > best_words:
                    best, best_words = list(steps), len(key_words)
        return best

    def to_prompt(self, request: str, limit: int = MAX_RECIPES) -> str:
        """The block that goes in the prompt. Empty when nothing matches."""
        matched = self.find(request, limit)
        if not matched:
            return ""
        return ("Notes you have written about how to do this:\n\n"
                + "\n\n".join(recipe.to_prompt() for recipe in matched))


def load(folder: Path | None = None) -> Shelf:
    """Read the recipes. Never raises - a bad file is reported, not fatal.

    Two folders unless one is named: the ones shipped with Meow, and the
    user's own in Documents. The user's are read second and win on a title
    clash, so a shipped recipe can be corrected by writing a better one rather
    than by editing the installation - which would be lost on the next pull.

    Not raising matters: recipes are hand-edited, so a half-finished one is
    the normal state of the folder at any moment, and a typo in a note about
    PowerPoint must not stop the cat opening a window.
    """
    if folder is not None:
        folders = [folder]
    else:
        folders = [shipped_folder(), recipe_folder()]

    shelf = Shelf()
    paths: list[Path] = []
    for where in folders:
        try:
            paths.extend(sorted(where.glob("*.md")))
        except Exception as error:  # noqa: BLE001
            shelf.skipped.append((str(where),
                                  f"{type(error).__name__}: {error}"))

    by_title: dict[str, Recipe] = {}
    for path in paths:
        try:
            recipe = parse(path.read_text(encoding="utf-8"), path=str(path))
        except Exception as error:  # noqa: BLE001
            shelf.skipped.append((path.name, f"{type(error).__name__}: {error}"))
            continue
        if recipe is None:
            shelf.skipped.append((path.name, "no heading or no body"))
            continue
        # Later folders override earlier ones by title.
        by_title[recipe.title.strip().lower()] = recipe

    shelf.recipes = list(by_title.values())
    return shelf
