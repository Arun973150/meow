"""Where files go on somebody else's machine.

Path bugs are silent. Nothing throws when an application saves a spreadsheet
into a Documents folder the user cannot see - it reports success, the file
exists, and only a person looking for it ever finds out.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from meow import storage
from meow.storage import paths


def test_documents_is_the_folder_the_shell_reports():
    """Not %USERPROFILE%\\Documents.

    Windows' Known Folder Move points Documents at OneDrive once backup is
    switched on. Both folders then exist, so writing to the guess fails
    silently: the file lands in a real directory that Explorer does not show.
    """
    shell = paths._known_folder(paths._FOLDERID_DOCUMENTS)
    if shell is None:
        pytest.skip("the shell would not answer")
    assert storage.documents() == shell / "Meow"


def test_the_database_is_not_under_a_synced_folder():
    """WAL is a second file that must stay consistent with the first, and a
    sync service copies them independently. That corrupts databases.
    """
    database = str(storage.conversations_database()).lower()
    assert "onedrive" not in database
    assert "dropbox" not in database
    # LOCALAPPDATA is the folder Windows guarantees is neither roamed nor
    # synced - which is exactly the guarantee a database needs.
    local = os.environ.get("LOCALAPPDATA")
    if local:
        assert database.startswith(local.lower())


def test_user_files_and_app_files_are_kept_apart():
    """Different owners, different folders. The user opens one and never the
    other, which is the whole reason for the split.
    """
    assert storage.documents() != storage.app_data()
    assert storage.recipes_folder().is_relative_to(storage.documents())
    assert storage.contacts_file().is_relative_to(storage.documents())
    assert storage.plans_database().is_relative_to(storage.app_data())
    assert storage.install_id_file().is_relative_to(storage.app_data())


def test_every_folder_exists_once_asked_for():
    for folder in (storage.documents(), storage.app_data(),
                   storage.cache(), storage.recipes_folder()):
        assert folder.is_dir()


def test_migration_never_overwrites(tmp_path, monkeypatch):
    """Two conversation stores with overlapping autoincrement ids do not
    merge. Leaving the old copy alone is the only honest option; the
    alternative loses messages without saying so.
    """
    old_home = tmp_path / "profile"
    old_store = old_home / "Documents" / "Meow"
    old_store.mkdir(parents=True)
    (old_store / "conversations.db").write_text("OLD", encoding="utf-8")

    destination = tmp_path / "appdata" / "Meow"
    destination.mkdir(parents=True)
    (destination / "conversations.db").write_text("ALREADY HERE",
                                                  encoding="utf-8")

    monkeypatch.setenv("USERPROFILE", str(old_home))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    storage.migrate_old_layout()

    assert (destination / "conversations.db").read_text(encoding="utf-8") \
        == "ALREADY HERE"
    assert (old_store / "conversations.db").exists(), "the old copy was lost"


def test_migration_brings_the_users_recipes(tmp_path, monkeypatch):
    """Directories were skipped at first, which left behind the one feature
    whose whole point is that it belongs to the user.
    """
    old_home = tmp_path / "profile"
    old_recipes = old_home / "Documents" / "Meow" / "Recipes"
    old_recipes.mkdir(parents=True)
    (old_recipes / "mine.md").write_text("# a recipe", encoding="utf-8")

    monkeypatch.setenv("USERPROFILE", str(old_home))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    # Force documents() somewhere that is not the old folder, so the move is
    # the one a Known-Folder-Move machine actually performs.
    monkeypatch.setattr(paths, "_known_folder",
                        lambda _id: tmp_path / "shell-documents")

    moved = storage.migrate_old_layout()
    landed = tmp_path / "shell-documents" / "Meow" / "Recipes" / "mine.md"
    assert landed.is_file(), f"recipe left behind; moved={moved}"
    assert landed.read_text(encoding="utf-8") == "# a recipe"


def test_a_locked_file_is_reported_not_swallowed(tmp_path, monkeypatch):
    """The chat window is its own process and holds conversations.db, so the
    file that matters most is the one most likely to be stuck. Two databases
    with no hint which is live is the worst outcome of a move.
    """
    old_home = tmp_path / "profile"
    old_store = old_home / "Documents" / "Meow"
    old_store.mkdir(parents=True)
    (old_store / "plans.db").write_text("x", encoding="utf-8")

    monkeypatch.setenv("USERPROFILE", str(old_home))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(Path, "replace",
                        lambda self, other: (_ for _ in ()).throw(OSError("in use")))

    storage.migrate_old_layout()
    assert "plans.db" in storage.stuck_in_the_old_layout()
