"""Windows and applications - beyond the one in front.

Everything else in Meow is scoped to the foreground window, which is right for
"click the save button" and useless for "open chrome". Three things are needed
to widen that, and they are different problems:

**Switching to a window that already exists.** Cheap, instant, and trivially
reversible - the user can alt-tab back. Treated as safe.

**Launching one that does not.** Found the way a person would: by the name on
the Start menu, not by guessing an executable path. `chrome` should find
"Google Chrome" without anyone typing `chrome.exe`.

**Knowing what is available at all.** Without this the model invents plausible
application names and asks to open things that are not installed.

`SetForegroundWindow` deserves a note. Windows refuses it from a process that
does not own the current foreground window - a rule that exists to stop
applications stealing focus, and which makes it fail silently for exactly the
legitimate case here. The way round it is to attach this thread's input queue to
the foreground window's thread first, which makes Windows treat them as the same
input context. Without that, focus changes work about half the time and there is
nothing in the return value to say why.
"""

from __future__ import annotations

import ctypes
import os
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SW_RESTORE = 9
SW_SHOW = 5
SW_MINIMIZE = 6
GW_OWNER = 4

user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# Where Windows keeps the things a person sees in their Start menu.
START_MENU_FOLDERS = (
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
)

# Shortcuts that are never what someone means by "open X".
IGNORED_SHORTCUTS = ("uninstall", "readme", "help", "documentation",
                     "license", "release notes", "website", "support")


# Words that appear in a request and say nothing about which application is
# meant. Kept short deliberately - over-filtering removes the word that
# mattered.
_FILLER = frozenset((
    "the", "a", "an", "my", "that", "this", "please", "open", "start",
    "launch", "up", "for", "and", "app", "application", "program",
))


@dataclass(frozen=True)
class Window:
    handle: int
    title: str
    process: str

    def describe(self) -> str:
        return f"{self.process}: {self.title[:60]}"


@dataclass(frozen=True)
class Application:
    name: str
    path: Path

    def describe(self) -> str:
        return self.name


def _process_name(handle: int) -> str:
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
    process = kernel32.OpenProcess(0x1000, False, process_id.value)
    if not process:
        return "?"
    try:
        buffer = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if kernel32.QueryFullProcessImageNameW(process, 0, buffer,
                                               ctypes.byref(size)):
            return buffer.value.rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(process)
    return "?"


def list_windows() -> list[Window]:
    """Every visible top-level window with a title.

    Owned windows are skipped: a dialog belonging to an application is not a
    separate thing to switch to, and including them fills the list with
    tooltips and popups.
    """
    found: list[Window] = []

    def visit(handle, _lparam):
        if not user32.IsWindowVisible(handle):
            return True
        if user32.GetWindow(handle, GW_OWNER):
            return True
        length = user32.GetWindowTextLengthW(handle)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        title = buffer.value.strip()
        if title and title != "Program Manager":
            found.append(Window(int(handle), title, _process_name(handle)))
        return True

    user32.EnumWindows(ENUM_WINDOWS_PROC(visit), 0)
    return found


def focus_window(window: Window) -> bool:
    """Bring a window to the front. True if it actually came forward.

    The AttachThreadInput dance is not superstition. Windows blocks
    SetForegroundWindow from a process that does not already own the
    foreground, and returns a value that does not distinguish "refused" from
    "worked". Attaching to the current foreground thread first puts this
    process in the same input context, which is what makes the call stick.
    """
    handle = window.handle
    user32.ShowWindow(handle, SW_RESTORE)

    foreground = user32.GetForegroundWindow()
    if foreground == handle:
        return True

    our_thread = kernel32.GetCurrentThreadId()
    their_thread = user32.GetWindowThreadProcessId(foreground, None)

    attached = False
    if their_thread and their_thread != our_thread:
        attached = bool(user32.AttachThreadInput(our_thread, their_thread, True))
    try:
        user32.SetForegroundWindow(handle)
        user32.ShowWindow(handle, SW_SHOW)
    finally:
        if attached:
            user32.AttachThreadInput(our_thread, their_thread, False)

    if user32.GetForegroundWindow() == handle:
        return True

    # Last resort: minimise it, then restore it. Restoring a minimised window
    # brings it forward WITHOUT SetForegroundWindow, which Windows refuses
    # outright when the caller does not already own the foreground - and the
    # attach dance above does not always lift that refusal.
    #
    # Measured: three of six applications in the evaluation suite could not be
    # focused any other way, and each failure left the PREVIOUS application in
    # front, so the wrong window was the one about to be measured.
    #
    # It flickers, which is why it is the fallback rather than the method. The
    # sleep is not optional: a window still mid-restore reports a zero-size
    # rect and walks to nothing, which reads exactly like an empty window.
    user32.ShowWindow(handle, SW_MINIMIZE)
    user32.ShowWindow(handle, SW_RESTORE)
    # Until it is actually in front, and no longer. This was a flat 0.7s,
    # chosen because 0.25s was too short for File Explorer's restore
    # animation - so every other window paid for Explorer.
    deadline = time.perf_counter() + 0.7
    while time.perf_counter() < deadline:
        if user32.GetForegroundWindow() == handle:
            return True
        time.sleep(0.04)
    return user32.GetForegroundWindow() == handle


def find_window(text: str) -> Window | None:
    """The best open window matching a description.

    Matches the title and the process name, because people say both - "my
    chrome" and "the aws tab" should reach the same window.
    """
    wanted = text.lower().strip()
    if not wanted:
        return None
    windows = list_windows()

    for test in (
        lambda w: wanted == w.title.lower(),
        lambda w: wanted in w.title.lower(),
        lambda w: wanted in w.process.lower().removesuffix(".exe"),
    ):
        matches = [w for w in windows if test(w)]
        if matches:
            return min(matches, key=lambda w: len(w.title))

    words = {word for word in wanted.split() if len(word) > 2}
    for window in windows:
        haystack = f"{window.title} {window.process}".lower()
        if any(word in haystack for word in words):
            return window
    return None


def _app_paths() -> dict[str, Path]:
    """Applications that registered themselves under App Paths.

    This is how `start chrome` resolves without a full path, and it is the
    cheapest source - a registry read rather than a directory walk.
    """
    found: dict[str, Path] = {}
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(
                root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths")
        except OSError:
            continue
        try:
            for index in range(winreg.QueryInfoKey(key)[0]):
                try:
                    name = winreg.EnumKey(key, index)
                    with winreg.OpenKey(key, name) as entry:
                        path = winreg.QueryValueEx(entry, "")[0]
                    if path:
                        found[name.removesuffix(".exe").lower()] = Path(path)
                except OSError:
                    continue
        finally:
            key.Close()
    return found


def _start_menu_apps() -> dict[str, Path]:
    """Shortcuts as a person sees them, by their friendly names.

    The Start menu is the right index for "open chrome": it is named the way
    the user thinks - "Google Chrome" - rather than the way the filesystem does.
    """
    found: dict[str, Path] = {}
    for folder in START_MENU_FOLDERS:
        if not folder.exists():
            continue
        for shortcut in folder.rglob("*.lnk"):
            name = shortcut.stem.lower()
            if any(ignored in name for ignored in IGNORED_SHORTCUTS):
                continue
            found.setdefault(name, shortcut)
    return found


def list_applications() -> list[Application]:
    """Everything that could be opened by name."""
    combined: dict[str, Path] = {}
    combined.update(_app_paths())
    # Start menu names win: they are what a person would say out loud.
    combined.update(_start_menu_apps())
    return [Application(name, path) for name, path in sorted(combined.items())]


# Windows' own settings pages are not executables and are not in the Start menu
# index, so nothing in `list_applications` can ever match them. Asked to "open
# settings", the search found the only installed thing containing the word -
# which on this machine is **WSL Settings** - and opened that instead, twice,
# while insisting it had tried.
#
# These are URI protocols handled by the shell. `ms-settings:` opens Settings;
# `ms-settings:personalization-colors` opens the page dark mode is on, which is
# what "how do i turn on dark mode" should be able to reach directly.
SHELL_TARGETS = {
    "settings": "ms-settings:",
    "windows settings": "ms-settings:",
    "system settings": "ms-settings:",
    "windows system settings": "ms-settings:",
    "display settings": "ms-settings:display",
    "sound settings": "ms-settings:sound",
    "bluetooth": "ms-settings:bluetooth",
    "bluetooth settings": "ms-settings:bluetooth",
    "wifi": "ms-settings:network-wifi",
    "wi-fi": "ms-settings:network-wifi",
    "wifi settings": "ms-settings:network-wifi",
    "network settings": "ms-settings:network",
    "dark mode": "ms-settings:personalization-colors",
    "colours": "ms-settings:personalization-colors",
    "colors": "ms-settings:personalization-colors",
    "personalisation": "ms-settings:personalization",
    "personalization": "ms-settings:personalization",
    "windows update": "ms-settings:windowsupdate",
    "installed apps": "ms-settings:appsfeatures",
    "default apps": "ms-settings:defaultapps",
    # Windows' own bundled apps. None of these is an .exe in the Start menu
    # index, so the installed search finds nothing and says "there is no
    # camera application installed" about a machine that ships with one.
    "camera": "microsoft.windows.camera:",
    "the camera": "microsoft.windows.camera:",
    "photos": "ms-photos:",
    "calculator": "calculator:",
    "maps": "bingmaps:",
    "store": "ms-windows-store:",
    "microsoft store": "ms-windows-store:",
    "clock": "ms-clock:",
    "alarms": "ms-clock:",
    "mail": "ms-mail:",
    "people": "ms-people:",
    "control panel": "control.exe",
    "task manager": "taskmgr.exe",
    "device manager": "devmgmt.msc",
    "recycle bin": "shell:RecycleBinFolder",
    "downloads": "shell:Downloads",
    "documents": "shell:Personal",
    "this pc": "shell:MyComputerFolder",
}


def find_shell_target(text: str) -> str | None:
    """A Windows shell URI for this name, if there is one.

    Checked BEFORE the installed-application search, because the installed
    search will confidently return something wrong: there is no Settings.exe,
    so "settings" matched "WSL Settings" and nothing about that reads as a
    failure.
    """
    wanted = " ".join(str(text).lower().split()).strip()
    wanted = wanted.removeprefix("open ").removeprefix("the ").strip(" .?!")
    if not wanted:
        return None
    if wanted in SHELL_TARGETS:
        return SHELL_TARGETS[wanted]
    # "windows settings please", "settings app"
    for name, target in SHELL_TARGETS.items():
        if wanted == name + " app" or wanted == "the " + name:
            return target
    return None


def open_shell_target(target: str) -> bool:
    """Open a shell URI or a system tool. True if Windows accepted it."""
    try:
        os.startfile(target)  # noqa: S606 - opening a shell URI is the point
        return True
    except OSError:
        return False


def find_application(text: str) -> Application | None:
    """The best installed application matching a spoken name."""
    wanted = text.lower().strip().removeprefix("open ").strip()
    if not wanted:
        return None
    apps = list_applications()

    for test in (
        lambda a: a.name == wanted,
        lambda a: a.name.startswith(wanted),
        lambda a: wanted in a.name,
    ):
        matches = [a for a in apps if test(a)]
        if matches:
            # Shortest name wins: "chrome" should find "Google Chrome" rather
            # than "Google Chrome Canary Developer Build".
            return min(matches, key=lambda a: len(a.name))

    # Filler words have to go before the overlap test. "the purple wombat"
    # matched "What is new in the latest version" on the strength of "the"
    # alone, which is how an assistant ends up opening release notes because
    # someone said a definite article.
    words = {word for word in wanted.split()
             if len(word) > 2 and word not in _FILLER}
    if not words:
        return None

    scored = [(len(words & set(a.name.split())), a) for a in apps]
    best = max(scored, key=lambda pair: pair[0], default=(0, None))
    return best[1] if best[0] else None


def launch(application: Application) -> bool:
    """Start an application. True if Windows accepted it."""
    try:
        os.startfile(str(application.path))  # noqa: S606 - the point of this
        return True
    except OSError:
        return False
