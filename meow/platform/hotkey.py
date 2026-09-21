"""Global activation key.

`RegisterHotKey`, not a `WH_KEYBOARD_LL` hook. The hook is the usual way to read
keys globally and it has a defect that disqualifies it here: it **stops
delivering events while a Chromium window has focus** - Chrome, VS Code, Slack,
Discord, Teams. Activation that silently dies in the apps people spend the most
time in is worse than no activation.

Tapped, never held. That distinction is the whole of invariant 9, and it is
narrower than "no chords": Clicky holds ctrl+option down for the entire length
of an utterance, and a sustained two-key hold is what hurts with tremor or
arthritis. Meow taps once to toggle, so the keys are down for a moment rather
than a sentence.

Sticky Keys keeps working, which is the real accessibility answer here.
RegisterHotKey reads the modifier STATE rather than the physical press, so
someone who cannot hold two keys at once can turn on Sticky Keys and press Ctrl
then M in sequence. A raw keyboard hook would not give that for free.

MOD_NOREPEAT is always set. Without it a key held down fires continuously, and
an activation toggle would flicker on and off for as long as it is held.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

user32.RegisterHotKey.argtypes = [
    wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

MODIFIER_CODES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}

KEY_CODES = {
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "scrolllock": 0x91,
    "pause": 0x13,
    "insert": 0x2D,
    "backquote": 0xC0,
}
KEY_CODES.update({chr(code): code for code in range(ord("A"), ord("Z") + 1)})
KEY_CODES.update({chr(code).lower(): code for code in range(ord("A"), ord("Z") + 1)})

# Combinations known to be load-bearing inside common applications. Registering
# one takes it away from that application system-wide for as long as Meow runs,
# which looks like the other application breaking, not like Meow working.
CONTESTED_COMBINATIONS = {
    "ctrl+space": "VS Code trigger-suggest, and IME language switching",
    "ctrl+shift+p": "the command palette in every editor",
    "ctrl+c": "copy",
    "ctrl+v": "paste",
    "ctrl+z": "undo",
    "alt+tab": "the window switcher",
    "ctrl+f": "find",
    "ctrl+s": "save",
}


def parse_combination(text: str) -> tuple[int, int, str]:
    """Turn "ctrl+m" into (modifiers, virtual key code, canonical name).

    A TAPPED chord is allowed here; a HELD one is not, and the difference is
    the whole of invariant 9. Clicky holds ctrl+option down for the length of
    the utterance, which is what hurts with tremor or arthritis. Meow taps once
    to toggle, so the keys are down for a moment rather than a sentence.

    Windows Sticky Keys also applies: RegisterHotKey reads the modifier state,
    not the physical press, so a user who cannot press two keys at once can
    enable Sticky Keys and press Ctrl then M in sequence. That is the real
    answer for the accessibility case, and it comes for free with this API but
    NOT with a raw keyboard hook.
    """
    parts = [part.strip().lower() for part in text.split("+") if part.strip()]
    if not parts:
        raise ValueError("empty key combination")

    modifiers = 0
    for part in parts[:-1]:
        if part not in MODIFIER_CODES:
            raise ValueError(
                f"unknown modifier {part!r}. Known: "
                f"{', '.join(sorted(set(MODIFIER_CODES)))}"
            )
        modifiers |= MODIFIER_CODES[part]

    key_name = parts[-1]
    if key_name not in KEY_CODES:
        raise ValueError(
            f"unknown key {key_name!r}. Known: a-z, space, enter, tab, "
            f"f8-f11, scrolllock, pause, insert, backquote"
        )

    canonical = "+".join(parts)
    return modifiers, KEY_CODES[key_name], canonical


class HotkeyUnavailable(RuntimeError):
    """Another process already owns this key.

    Raised rather than ignored: a silently unregistered hotkey looks exactly
    like a broken application to the user, who presses the key and gets nothing.
    """


@dataclass(frozen=True)
class Hotkey:
    identifier: int
    name: str
    modifiers: int
    key_code: int

    @property
    def display_name(self) -> str:
        return "+".join(part.upper() if len(part) == 1 else part.capitalize()
                        for part in self.name.split("+"))


class HotkeyListener:
    """Registers global keys and reports presses.

    Owns the thread message pump, because a hotkey registered with a NULL window
    handle is posted to the THREAD queue and never dispatched to any window.
    Anything else draining the queue would swallow the presses, so this replaces
    `Overlay.pump_messages` rather than running alongside it.
    """

    def __init__(self) -> None:
        self._hotkeys: dict[int, Hotkey] = {}
        self._next_identifier = 1

    def register(self, combination: str) -> Hotkey:
        """Claim a key or combination globally.

        Warns on combinations that another application is likely to need. It
        still registers - the user asked for it - but silently stealing
        Ctrl+Space from an editor is the kind of thing that gets blamed on the
        editor.
        """
        modifiers, key_code, canonical = parse_combination(combination)

        contested = CONTESTED_COMBINATIONS.get(canonical)
        if contested:
            print(f"  note: {canonical} is normally {contested}. "
                  f"It will not reach that application while Meow is running.")

        identifier = self._next_identifier
        self._next_identifier += 1

        # MOD_NOREPEAT: without it a held key fires continuously and an
        # activation toggle flickers on and off for as long as it is down.
        if not user32.RegisterHotKey(
            None, identifier, modifiers | MOD_NOREPEAT, key_code
        ):
            raise HotkeyUnavailable(
                f"could not register {canonical!r} (error "
                f"{ctypes.get_last_error()}). Another application already holds "
                f"it - pick a different combination."
            )

        hotkey = Hotkey(identifier, canonical, modifiers, key_code)
        self._hotkeys[identifier] = hotkey
        return hotkey

    def pump(self) -> list[Hotkey]:
        """Drain the message queue, returning any hotkeys pressed.

        Also dispatches ordinary window messages, so this is the only pump the
        caller needs.
        """
        pressed: list[Hotkey] = []
        message = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_REMOVE):
            if message.message == WM_HOTKEY:
                hotkey = self._hotkeys.get(int(message.wParam))
                if hotkey is not None:
                    pressed.append(hotkey)
                continue
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        return pressed

    def close(self) -> None:
        for identifier in list(self._hotkeys):
            user32.UnregisterHotKey(None, identifier)
            del self._hotkeys[identifier]

    def __enter__(self) -> "HotkeyListener":
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()
