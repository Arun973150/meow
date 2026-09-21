"""When to ask, and when asking is just noise.

The original rule was "confirm anything that changes the machine", and used for
five minutes it is obviously wrong: told to open notepad, the cat asked "open
notepad?" - repeating the instruction back and waiting. Nobody reads the tenth
prompt of a session, which means a blanket confirm does not make anything safer,
it makes the one prompt that mattered invisible.

So two questions, in this order, and the order is the whole design:

**Is this dangerous?** If so, ask - always, and regardless of how clearly it was
asked for. Saying "delete them" out loud is not the same as knowing what "them"
turned out to be, and the cost of a wrong delete is not symmetrical with the
cost of one extra question.

**Did they actually say this?** If the instruction named the thing, the
confirmation is a repetition. "Open notepad" -> open_app("Notepad") is the
user's own sentence read back to them.

Everything else is asked about, because it is the cat's inference rather than
the user's instruction.

Danger is judged on the ACTION, not on the sentence. Jev classifies what was
said, which is necessary and not sufficient: "click that one" is a harmless
sentence, and if that one turns out to be "Delete All Messages" the danger is
in the control, not the request. Both are checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Decision(Enum):
    ASK = "ask"
    PROCEED = "proceed"


@dataclass(frozen=True)
class Judgement:
    decision: Decision
    why: str

    @property
    def should_ask(self) -> bool:
        return self.decision is Decision.ASK


# Words that make an action worth a question however it was phrased. Matched
# against the TARGET - the control name, the app, the text being typed - not
# against the user's sentence.
#
# Deliberately broad. A false ask costs one question; a false proceed costs
# whatever the button did, and some of these buttons are not undoable.
DANGEROUS = (
    # destroying things
    "delete", "remove", "erase", "wipe", "purge", "destroy", "discard",
    "clear all", "empty", "format", "uninstall", "reset", "factory",
    "revert", "overwrite", "replace all", "don't save", "do not save",
    "without saving", "unsaved",
    # sending things to other people
    "send", "post", "publish", "share", "submit", "reply all", "forward",
    "invite", "broadcast", "tweet",
    # money
    "buy", "purchase", "pay", "order", "checkout", "subscribe", "upgrade",
    "confirm payment", "place order",
    # identity and access
    "sign out", "log out", "logout", "revoke", "deauthorize", "unlink",
    "disconnect", "change password", "permissions", "grant access",
    # the machine itself
    "shut down", "shutdown", "restart", "reboot", "sign off", "power off",
    "end task", "kill",
    # code and infrastructure
    "deploy", "force push", "merge", "rebase", "drop table", "terminate",
)

# Whole words that are dangerous alone but appear inside innocent ones -
# "remove" in "remove filter", "kill" in "killer". Matched with boundaries.
_DANGEROUS_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(word) for word in DANGEROUS) + r")\b",
    re.IGNORECASE,
)

# Tools that never change anything. Pointing at a control and reading the
# screen are not decisions.
HARMLESS_TOOLS = frozenset({
    "point_at_control", "list_controls", "list_open_windows",
    "switch_to_window",
})

# Keyboard shortcuts that do something serious. A shortcut hides its meaning -
# "press ctrl+w" is not obviously "close this without asking".
DANGEROUS_SHORTCUTS = frozenset({
    "ctrl+w", "ctrl+shift+w", "alt+f4", "ctrl+shift+delete", "shift+delete",
    "ctrl+alt+delete", "win+l", "ctrl+q",
})


# Words that appear in an instruction and identify nothing.
_FILLER = frozenset((
    "the", "a", "an", "my", "that", "this", "it", "one", "thing", "please",
    "can", "you", "to", "go", "on", "in", "of", "for", "and", "uh", "um",
    "open", "start", "launch", "click", "press", "type", "switch", "show",
    "me", "i", "want", "would", "like", "just", "button", "control", "app",
))


def _words(text: str) -> set[str]:
    return {word.strip(".,!?()[]\"'`+-").lower() for word in text.split()}


def names_the_target(transcript: str, target: str) -> bool:
    """Did the user's own sentence name this thing?

    The test is whether a distinctive word of the TARGET appears in what they
    said. Both other directions were tried and both were wrong.

    Requiring every target word made the user confirm their own instruction
    because of a version number: "open word" does not contain "Microsoft" or
    "2016".

    Requiring most of the SENTENCE to be about the target broke on compound
    instructions, which is how most people speak. "Can you open Notepad and
    type hello there" scores one matching word in three when judging the
    launch, because the other two belong to the second half of the sentence -
    so the cat asked permission to open notepad immediately after being told
    to open notepad.

    Four characters is the floor for a word counting as distinctive. Below it,
    matching is coincidence - "OK", "No", a single letter - and those ask.
    """
    if not target.strip():
        return False

    said = _words(transcript)
    for word in _words(target.replace("+", " ")):
        if len(word) < 4 or word in _FILLER:
            continue
        if word in said or any(word in spoken for spoken in said):
            return True
    return False


def is_dangerous(tool: str, target: str) -> bool:
    """Would getting this wrong be hard to undo?"""
    if tool == "press_keys":
        return target.strip().lower().replace(" ", "") in DANGEROUS_SHORTCUTS
    return bool(_DANGEROUS_PATTERN.search(target))


def judge(tool: str, target: str, transcript: str,
          route_risky: bool = False) -> Judgement:
    """Ask or proceed, and why.

    `route_risky` is Jev's opinion of the SENTENCE. It is used as one more
    reason to ask, never as a reason to skip asking - a sentence can sound
    harmless and still resolve to a destructive control.
    """
    if tool in HARMLESS_TOOLS:
        return Judgement(Decision.PROCEED, "changes nothing")

    if is_dangerous(tool, target):
        # Not overridable by clarity. "Delete everything" is clear and that is
        # not a reason to skip the question.
        return Judgement(Decision.ASK, "this one is hard to undo")

    if route_risky:
        return Judgement(Decision.ASK, "sounded risky")

    if names_the_target(transcript, target):
        return Judgement(Decision.PROCEED, "you asked for this by name")

    return Judgement(Decision.ASK, "not what you literally asked for")
