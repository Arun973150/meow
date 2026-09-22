"""Where things go on a machine that belongs to somebody else.

Three places, not one, because three different things were being kept in the
same folder and they have different owners:

    documents()   things the USER opens - .docx, .xlsx, recipes they wrote,
                  the contacts file they type into. Must be somewhere they
                  can find in Explorer.
    app_data()    things the APP owns - conversations.db, plans.db, the
                  install id. The user never opens these and should not have
                  to see them.
    cache()       things that can be deleted without losing anything.

Two bugs made this a module rather than six copies of one expression.

**Documents is not `%USERPROFILE%\\Documents`.** Windows' Known Folder Move
points it at OneDrive once backup is on, and on this machine it does: the
shell says `C:\\Users\\ADMIN\\OneDrive\\Documents` while the old code wrote to
`C:\\Users\\ADMIN\\Documents`. Both exist, so nothing failed - the app simply
saved a spreadsheet into a Documents folder that File Explorer does not show,
after announcing that everything lands in Documents/Meow. Ask the shell.

**A SQLite database must not live in a synced folder.** WAL is a second file
that has to stay consistent with the first, and a file-sync service copies
them independently and on its own schedule; that corrupts databases. The old
path avoided this by accident - it wrote to the un-synced twin. Now that
documents() correctly resolves into OneDrive, putting the database there would
introduce the corruption the old bug was hiding. Databases go to
LOCALAPPDATA, which Windows guarantees is neither roamed nor synced.

That separation is also the seam for hosting any of this later: `documents()`
is files and belongs to file sync, while `app_data()` is state, and state is
what a server would hold. Nothing here reaches a network, and the split means
adding that later does not have to move the user's spreadsheets.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import uuid
from pathlib import Path

APPLICATION = "Meow"


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str) -> None:
        parsed = uuid.UUID(text)
        super().__init__()
        self.Data1 = parsed.time_low
        self.Data2 = parsed.time_mid
        self.Data3 = parsed.time_hi_version
        for index, byte in enumerate(parsed.bytes[8:]):
            self.Data4[index] = byte


_FOLDERID_DOCUMENTS = "FDD39AD0-238F-46AF-ADB4-6C85480369C7"


def _known_folder(folder_id: str) -> Path | None:
    """Ask Windows where a known folder actually is, or None."""
    result = ctypes.c_wchar_p()
    try:
        status = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(_GUID(folder_id)), 0, None, ctypes.byref(result))
    except Exception:  # noqa: BLE001 - not Windows, or shell32 unavailable
        return None
    if status != 0 or not result.value:
        return None
    try:
        return Path(result.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(result)


def documents() -> Path:
    """Where the user's own Meow files go. Created if missing."""
    base = _known_folder(_FOLDERID_DOCUMENTS)
    if base is None:
        # Only when the shell will not answer. Still better than nothing, and
        # it is the path the app used to use, so an old install lands home.
        base = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"
    folder = base / APPLICATION
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def app_data() -> Path:
    """Where the app's own state goes. Never synced, never roamed."""
    base = os.environ.get("LOCALAPPDATA")
    folder = (Path(base) if base else Path.home() / ".meow") / APPLICATION
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def cache() -> Path:
    """Deletable. Nothing here is the only copy of anything."""
    folder = app_data() / "cache"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# --- the specific things -------------------------------------------------

def conversations_database() -> Path:
    return app_data() / "conversations.db"


def plans_database() -> Path:
    return app_data() / "plans.db"


def install_id_file() -> Path:
    return app_data() / "install-id"


def recipes_folder() -> Path:
    """The user writes these, so they live where they can find them."""
    folder = documents() / "Recipes"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def contacts_file() -> Path:
    """Typed by hand, because a dictated address does not survive."""
    return documents() / "contacts.txt"


# --- moving an existing install -------------------------------------------

# What used to be in one folder, and where each piece belongs now. Databases
# move; documents stay documents, but the FOLDER they were in may have been
# the invisible twin, so those are copied across too.
_APP_FILES = ("conversations.db", "conversations.db-wal", "conversations.db-shm",
              "plans.db", "plans.db-wal", "plans.db-shm", "install-id",
              # A log is app state, not something the user opens. It sat in
              # Documents looking like a file somebody might want.
              "window.log")


def migrate_old_layout() -> list[str]:
    """Move an existing install into the new places. Safe to call always.

    Never overwrites: if something is already at the destination, the old copy
    is left alone rather than being merged. Two conversation databases with
    overlapping autoincrement ids do not merge, and pretending otherwise would
    lose messages silently.
    """
    moved: list[str] = []
    old = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents" / APPLICATION
    if not old.is_dir():
        return moved

    for name in _APP_FILES:
        source = old / name
        if not source.is_file():
            continue
        destination = app_data() / name
        if destination.exists():
            continue
        try:
            source.replace(destination)
            moved.append(name)
        except OSError:
            # In use, or across volumes. Not worth failing startup over - the
            # app makes a fresh one and the old is still there to recover.
            pass

    # Documents the user made, if the old folder was the invisible twin.
    here = documents()
    if old.resolve() != here.resolve():
        for source in old.iterdir():
            if source.name in _APP_FILES:
                continue
            destination = here / source.name
            if source.is_dir():
                # Recipes, and anything else the user keeps in folders. Merged
                # file by file rather than moved whole: the destination may
                # already hold recipes, and replacing the folder would delete
                # them. Directories were skipped entirely at first, which
                # quietly left the user's own recipes behind - the one feature
                # whose whole point is that it belongs to them.
                destination.mkdir(parents=True, exist_ok=True)
                for inner in source.rglob("*"):
                    if inner.is_dir():
                        continue
                    target = destination / inner.relative_to(source)
                    if target.exists():
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        inner.replace(target)
                        moved.append(str(inner.relative_to(old)))
                    except OSError:
                        pass
                continue
            if destination.exists():
                continue
            try:
                source.replace(destination)
                moved.append(source.name)
            except OSError:
                pass
    return moved


def stuck_in_the_old_layout() -> list[str]:
    """Files the move could not take, usually because something has them open.

    Worth asking separately rather than reporting inside the move: the chat
    window runs as its own process and holds conversations.db, so a migration
    while it is up leaves exactly the file that matters most. Silence there
    would mean two databases and no hint which one is live.
    """
    old = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents" / APPLICATION
    if not old.is_dir():
        return []
    left = []
    for name in _APP_FILES:
        if (old / name).is_file() and not (app_data() / name).exists():
            left.append(name)
    return left
