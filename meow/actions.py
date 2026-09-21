"""Doing things - Phase 1.5 and 1.7.

The point where Meow stops describing a computer and starts operating one.

**Every action declares its risk, and the risky ones ask first.** That is the
autonomy decision this project was built around - point freely, click with
permission - and it is not a setting. A user who cannot easily see what
happened, or easily undo it, needs to be asked before rather than told after;
designing for that makes the software better for everyone. See 03-safety.md.

**Two ways to press a button, and the second is the interesting one.**

`click()` moves the pointer and clicks, which is what a companion should
usually do: the user watches it happen and learns where the thing was.

`invoke()` calls the accessibility API directly. No pointer movement, no focus
change, and it works on a window that is not in front. It is faster, it cannot
miss, and it cannot be intercepted by something that happened to move under the
cursor between locating and clicking. Vision grounding cannot do this at all -
a guessed (x, y) has nothing to invoke - so it is a capability gap rather than
an accuracy gap, and worth reporting separately in the evaluation.

`invoke()` is preferred when the target came from UIA and the control supports
it. When it does not, this falls back to a click rather than refusing.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .grounding import Source, Target
from .pointing import glide_to

user32 = ctypes.WinDLL("user32", use_last_error=True)

# UIA pattern ids, from UIAutomationClient.h.
UIA_INVOKE_PATTERN = 10000
UIA_TOGGLE_PATTERN = 10015
UIA_EXPAND_COLLAPSE_PATTERN = 10005
UIA_VALUE_PATTERN = 10002
UIA_SELECTION_ITEM_PATTERN = 10010

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002


class Risk(Enum):
    """How much a thing matters if it was not what the user meant."""

    # Shows something. Changes nothing. Never asks.
    SAFE = "safe"
    # Changes the state of the machine in a way the user may not expect.
    # Always asks, unless the user asked for this exact thing by name.
    CONFIRM = "confirm"
    # Asks every single time, with no way to turn it off. Reserved for things
    # that can destroy work.
    ALWAYS_CONFIRM = "always_confirm"


@dataclass
class Outcome:
    """What happened, in words the cat can say out loud."""

    ok: bool
    detail: str
    method: str = ""
    refused: bool = False

    def __bool__(self) -> bool:
        return self.ok


# Asked "click the Send button?" and answers yes or no. The voice loop asks out
# loud; the evaluation harness auto-answers; a test can refuse everything.
Confirmer = Callable[[str], bool]


def always_allow(_question: str) -> bool:
    return True


def always_refuse(_question: str) -> bool:
    return False


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


def _send(*inputs: _INPUT) -> None:
    array = (_INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), array, ctypes.sizeof(_INPUT))


def _mouse_event(flags: int) -> _INPUT:
    return _INPUT(type=INPUT_MOUSE,
                  union=_INPUTUNION(mi=_MOUSEINPUT(0, 0, 0, flags, 0, None)))


def point_at(target: Target, glide_seconds: float = 0.55,
             should_stop=None) -> Outcome:
    """Move the pointer to the target. Shows, changes nothing."""
    x, y = target.centre
    reached = glide_to(x, y, seconds=glide_seconds, should_stop=should_stop)
    if not reached:
        return Outcome(False, "you moved the mouse, so it stopped",
                       method="glide")
    return Outcome(True, f"pointing at {target.name}", method="glide")


def click(target: Target, confirm: Confirmer,
          right_button: bool = False) -> Outcome:
    """Move the pointer to the target and click it."""
    question = (f"right-click {target.name}?" if right_button
                else f"click {target.name}?")
    if not confirm(question):
        return Outcome(False, f"The user declined. {target.name} was not "
                       f"pressed, and nothing is wrong with it.",
                       refused=True)

    moved = point_at(target)
    if not moved:
        return moved

    down = MOUSEEVENTF_RIGHTDOWN if right_button else MOUSEEVENTF_LEFTDOWN
    up = MOUSEEVENTF_RIGHTUP if right_button else MOUSEEVENTF_LEFTUP
    _send(_mouse_event(down))
    # A real click has a gap between press and release. Some controls ignore an
    # instantaneous one, and a few treat it as a drag of zero length.
    time.sleep(0.03)
    _send(_mouse_event(up))

    return Outcome(True, f"clicked {target.name}", method="click")


def invoke(target: Target, confirm: Confirmer) -> Outcome:
    """Operate the control through UIA, without moving the pointer.

    Falls back to clicking when the target came from vision, or when the
    control does not support being invoked. Refusing would be technically
    correct and useless to the user.
    """
    if not confirm(f"press {target.name}?"):
        # Worded for the model, not for a log. "left it alone" was read as
        # a malfunction and reported to the user as "the button did not
        # respond, it might be disabled" - which is alarming and false.
        return Outcome(False, f"The user declined. {target.name} was not "
                       f"pressed, and nothing is wrong with it.",
                       refused=True)

    element = target.element
    if target.source is not Source.UIA or element is None or element.node is None:
        return _click_without_asking(target, "vision target, so it clicks")

    node = element.node
    for pattern_id, call in (
        (UIA_INVOKE_PATTERN, "Invoke"),
        (UIA_TOGGLE_PATTERN, "Toggle"),
        (UIA_SELECTION_ITEM_PATTERN, "Select"),
        (UIA_EXPAND_COLLAPSE_PATTERN, "Expand"),
    ):
        try:
            pattern = node.GetCurrentPattern(pattern_id)
            if not pattern:
                continue
            getattr(pattern, call)()
            return Outcome(True, f"pressed {target.name}",
                           method=f"uia:{call.lower()}")
        except Exception:  # noqa: BLE001 - try the next pattern, then click
            continue

    return _click_without_asking(target, "no invoke pattern, so it clicks")


def _click_without_asking(target: Target, why: str) -> Outcome:
    """The click half of invoke(), after permission was already given.

    Asking twice for one action trains people to stop reading the question.
    """
    moved = point_at(target)
    if not moved:
        return moved
    _send(_mouse_event(MOUSEEVENTF_LEFTDOWN))
    time.sleep(0.03)
    _send(_mouse_event(MOUSEEVENTF_LEFTUP))
    return Outcome(True, f"clicked {target.name} ({why})", method="click")


def type_text(text: str, confirm: Confirmer,
              characters_per_second: int = 90) -> Outcome:
    """Type into whatever has focus.

    Unicode scan codes rather than virtual keys, so this is independent of the
    user's keyboard layout - a virtual-key approach types garbage on anything
    but the layout it was written for.
    """
    if not text:
        return Outcome(False, "nothing to type")

    preview = text if len(text) <= 40 else text[:40] + "..."
    if not confirm(f'type "{preview}"?'):
        return Outcome(False, "The user declined, so nothing was typed.",
                       refused=True)

    delay = 1.0 / max(1, characters_per_second)
    for character in text:
        code = ord(character)
        _send(_INPUT(type=INPUT_KEYBOARD, union=_INPUTUNION(
            ki=_KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, None))))
        _send(_INPUT(type=INPUT_KEYBOARD, union=_INPUTUNION(
            ki=_KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None))))
        time.sleep(delay)

    return Outcome(True, f"typed {len(text)} characters", method="sendinput")


# What each action costs if it was not what the user meant. Referenced by the
# harness so the risk level lives with the action rather than at the call site,
# where it would eventually be forgotten.
RISK = {
    point_at: Risk.SAFE,
    click: Risk.CONFIRM,
    invoke: Risk.CONFIRM,
    type_text: Risk.CONFIRM,
}
