"""Global activation key.

`RegisterHotKey`, not a `WH_KEYBOARD_LL` hook. The hook is the usual way to read
keys globally and it has a defect that disqualifies it here: it **stops
delivering events while a Chromium window has focus** - Chrome, VS Code, Slack,
Discord, Teams. Activation that silently dies in the apps people spend the most
time in is worse than no activation.

Single key, never a held chord. A sustained two-key hold is hostile to tremor
and arthritis, and the people this has to work for are the reason the design
exists at all. See AGENTS.md invariant 9 and 00-scope.md.

MOD_NOREPEAT is always set. Without it a key held down fires continuously, and
an activation toggle would flicker on and off for as long as the key is held.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
MOD_NOREPEAT = 0x4000

user32.RegisterHotKey.argtypes = [
    wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

# Keys that are safe to claim globally: a function key or a lock key is rarely
# load-bearing inside another application, where a letter always is.
KEY_CODES = {
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "scrolllock": 0x91,
    "pause": 0x13,
    "insert": 0x2D,
}


class HotkeyUnavailable(RuntimeError):
    """Another process already owns this key.

    Raised rather than ignored: a silently unregistered hotkey looks exactly
    like a broken application to the user, who presses the key and gets nothing.
    """


@dataclass(frozen=True)
class Hotkey:
    identifier: int
    name: str
    key_code: int


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

    def register(self, key_name: str) -> Hotkey:
        normalised = key_name.strip().lower()
        if normalised not in KEY_CODES:
            raise ValueError(
                f"unknown key {key_name!r}. Known: {', '.join(sorted(KEY_CODES))}"
            )

        identifier = self._next_identifier
        self._next_identifier += 1

        if not user32.RegisterHotKey(
            None, identifier, MOD_NOREPEAT, KEY_CODES[normalised]
        ):
            raise HotkeyUnavailable(
                f"could not register {normalised!r} (error "
                f"{ctypes.get_last_error()}). Another application already holds "
                f"it - pick a different key."
            )

        hotkey = Hotkey(identifier, normalised, KEY_CODES[normalised])
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
