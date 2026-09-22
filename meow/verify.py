"""Did it actually happen?

Phase 1.9. Until now the cat reported what it *attempted*. A real session log
reads:

    73.5s  says: i typed your name in the document.

It says that whether or not a single character arrived. `actions.type_text`
returns `Outcome(True, "typed 24 characters")` the moment SendInput accepts the
keystrokes, which tells us the input queue took them - not that a text field
received them, and not that the right window had focus. Every claim the cat
makes about the world was really a claim about its own intent.

**Checked through UIA, not a screenshot.** The phase plan said "produce ->
screenshot -> look_at -> repair", and this deviates on the strength of the
project's own ablation: vision scored 0/6 at locating controls while UIA scored
6/6 at 0px. Verification would have been the same model looking at the same
pixels, costing 2,833 tokens a turn to consult the strategy that lost. The tree
already knows whether the button is gone and what the field now contains.

It is also cheap. A snapshot is 9ms in steady state - 1.3ms for the window
list, 4.3ms for the focused element, 0.7ms to read its value - so checking an
action costs about 18ms for the pair. Against 268ms for a digest, or over a
second for a full VS Code walk, that is affordable on every single action,
which is what makes it usable inside a multi-step plan.

The first call is the exception: 130-260ms while COM initialises, once per
process. Worth knowing before blaming the first action of a session for being
slow.

**Three answers, and the third is the point.** A verdict is yes, no, or *could
not tell*, and the third is why this is worth having. A verifier that only ever
confirms is a more confident liar than no verifier at all. Clicking into a text
box changes nothing observable; that is not failure, and reporting it as
failure would train the user to ignore the reports. So `happened is None` says
so plainly, the tool passes the words to the model, and the cat says "i pressed
it, though i could not tell whether anything changed" - which is the true
sentence.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from . import apps

# Long values are compared, never repeated. A text field can hold a whole
# document and the comparison only needs enough to tell two states apart.
MAX_VALUE_CHARACTERS = 400


def _focused_element():
    """The control with keyboard focus, or None if nothing will say.

    Wrapped because this reaches COM on a live desktop: the focused element can
    be destroyed between the call and the read - a closing dialog does exactly
    that - and a verification step must never be the thing that breaks an
    action that already succeeded.
    """
    try:
        import uiautomation

        return uiautomation.GetFocusedControl()
    except Exception:  # noqa: BLE001 - absence is a valid answer here
        return None


def _value_of(element) -> str:
    """What a focused control currently contains, as text.

    Not every control exposes a value. An editor canvas, a custom-drawn grid
    and a button all decline, and declining is normal rather than an error -
    it becomes "could not tell" further up.
    """
    if element is None:
        return ""
    for getter in ("GetValuePattern", "GetLegacyIAccessiblePattern"):
        try:
            pattern = getattr(element, getter)()
            value = getattr(pattern, "Value", None)
            if value:
                return str(value)[:MAX_VALUE_CHARACTERS]
        except Exception:  # noqa: BLE001 - try the next pattern
            continue
    return ""


@dataclass(frozen=True)
class Snapshot:
    """The cheap, observable facts about the desktop at one instant."""

    foreground_title: str = ""
    foreground_process: str = ""
    windows: frozenset[tuple[str, str]] = frozenset()
    focused_role: str = ""
    focused_name: str = ""
    focused_value: str = ""

    @property
    def focus(self) -> tuple[str, str]:
        return (self.focused_role, self.focused_name)


def look() -> Snapshot:
    """Take a snapshot. 9ms once COM is warm, and never raises.

    Never raising is deliberate. This runs immediately before and after real
    actions, and an exception here would turn a successful click into a failed
    tool call - the verification breaking the thing it was checking.
    """
    try:
        windows = apps.list_windows()
    except Exception:  # noqa: BLE001
        windows = []

    # Matched out of the list we already have rather than queried separately.
    # GetForegroundWindow is a handle and nothing else, and the list carries
    # the title and process for it - so this costs one more comparison rather
    # than another enumeration.
    foreground_title = ""
    foreground_process = ""
    try:
        front_handle = ctypes.windll.user32.GetForegroundWindow()
        for window in windows:
            if window.handle == front_handle:
                foreground_title = window.title
                foreground_process = window.process
                break
    except Exception:  # noqa: BLE001
        pass

    element = _focused_element()
    role = name = ""
    if element is not None:
        try:
            role = str(element.ControlTypeName or "")
            name = str(element.Name or "")
        except Exception:  # noqa: BLE001
            pass

    return Snapshot(
        foreground_title=foreground_title,
        foreground_process=foreground_process,
        windows=frozenset((window.process, window.title) for window in windows),
        focused_role=role,
        focused_name=name,
        focused_value=_value_of(element),
    )


@dataclass(frozen=True)
class Verdict:
    """Yes, no, or could not tell - and a sentence saying which."""

    happened: bool | None
    detail: str

    @property
    def unknown(self) -> bool:
        return self.happened is None

    def phrase(self) -> str:
        """What gets appended to a tool result, for the model to read.

        Written so an unverified action reads as unverified. The model turns
        this into speech, and the whole point is that it stops claiming
        outcomes nobody checked.
        """
        if self.happened is True:
            return f"Verified: {self.detail}."
        if self.happened is False:
            return f"NOT verified: {self.detail}. Tell the user it did not work."
        return (f"Could not verify: {self.detail}. Say you did it but could "
                f"not confirm it - do not claim it worked.")


def typed(before: Snapshot, after: Snapshot, text: str) -> Verdict:
    """Did the characters land in a field?

    The strong check is containment: the focused control now holds the text and
    did not before. Editors that draw their own text - the VS Code buffer is
    one - expose no value at all, and the honest answer there is that we cannot
    tell, not that it failed.
    """
    wanted = " ".join(text.split())[:MAX_VALUE_CHARACTERS]
    if not wanted:
        return Verdict(None, "nothing was typed to check for")

    settled = " ".join(after.focused_value.split())
    started = " ".join(before.focused_value.split())

    if wanted.lower() in settled.lower():
        where = after.focused_name or after.focused_role or "the focused field"
        return Verdict(True, f'"{wanted[:40]}" is now in {where}')

    if not after.focused_value and not before.focused_value:
        return Verdict(None, "this control does not report its contents, "
                             "so the text cannot be read back")

    if settled != started:
        # Something arrived but not what we sent. Autocomplete, autocorrect and
        # a field that reformats as you type all do this, and so does typing
        # into the wrong window - worth flagging rather than calling success.
        return Verdict(None, f"the field changed but does not contain "
                             f'"{wanted[:40]}"')

    return Verdict(False, "the focused field is unchanged, so nothing arrived")


def opened(before: Snapshot, after: Snapshot, app_name: str) -> Verdict:
    """Is there a window now that was not there before?

    Matched loosely, because the name a user says rarely matches the process.
    "word" has to find WINWORD.EXE and "notepad" has to survive a window titled
    "Untitled - Notepad".
    """
    fresh = after.windows - before.windows
    if not fresh:
        return Verdict(False, f"no new window appeared for {app_name}")

    wanted = app_name.lower().replace(".exe", "").strip()
    for process, title in fresh:
        haystack = f"{process} {title}".lower()
        if wanted and (wanted in haystack
                       or any(word in haystack for word in wanted.split()
                              if len(word) > 3)):
            return Verdict(True, f"{title[:50]} opened")

    # A window appeared, but nothing about it says it is the one asked for.
    # Launchers and splash screens do this, so it is not a failure.
    process, title = sorted(fresh)[0]
    return Verdict(None, f"a window opened ({title[:40]}) but it does not "
                         f"look like {app_name}")


def switched(after: Snapshot, window_name: str) -> Verdict:
    """Is the window actually in front now?

    Windows refuses foreground changes from a process that is not already in
    front, so this one fails quietly and often - which is exactly the kind of
    silent failure the cat used to report as success.
    """
    wanted = window_name.lower().strip()
    front = f"{after.foreground_process} {after.foreground_title}".lower()
    if wanted and (wanted in front
                   or any(word in front for word in wanted.split()
                          if len(word) > 3)):
        return Verdict(True, f"{after.foreground_title[:50]} is in front")
    if not after.foreground_title:
        return Verdict(None, "cannot read which window is in front")
    return Verdict(False, f"{after.foreground_title[:40]} is still in front, "
                          f"not {window_name}")


def anything_changed(before: Snapshot, after: Snapshot,
                     what: str = "that") -> Verdict:
    """The weak check, for actions with no specific expected result.

    Pressing a button can do anything or nothing visible, so this reports
    whichever difference it can find and says "could not tell" when it finds
    none. It deliberately does not return False: no observable change is not
    evidence of failure, and claiming it is would make every click into a
    text box read as broken.
    """
    if before.windows != after.windows:
        appeared = after.windows - before.windows
        vanished = before.windows - after.windows
        if appeared:
            return Verdict(True, f"{sorted(appeared)[0][1][:40]} appeared")
        if vanished:
            return Verdict(True, f"{sorted(vanished)[0][1][:40]} closed")

    if before.foreground_title != after.foreground_title:
        return Verdict(True, f"the front window is now "
                             f"{after.foreground_title[:40]}")

    if before.focus != after.focus:
        moved_to = after.focused_name or after.focused_role or "something else"
        return Verdict(True, f"focus moved to {moved_to[:40]}")

    if before.focused_value != after.focused_value:
        return Verdict(True, "the focused field's contents changed")

    return Verdict(None, f"nothing observable changed after {what}")
