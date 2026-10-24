"""The files that ship with the app, and the three lists that must agree.

`storage/resources.py:SHIPPED` says what the app needs at runtime,
`pyproject.toml:package-data` puts it in a wheel, and `meow.spec` puts it in
a bundle. Any one of the three being out of step costs a feature SILENTLY -
which is how the recipes vanished when a path moved, and how the cat cursor
has been missing from every wheel ever built, because it sat outside the
package and nobody listed it.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest

from meow.storage import resources

ROOT = Path(__file__).resolve().parent.parent


def test_everything_shipped_is_actually_here():
    """Run from a source tree this is trivially true, and it is the check
    that fails the moment a file is moved without the list being updated."""
    assert resources.missing() == []


@pytest.mark.parametrize("name", resources.SHIPPED)
def test_each_shipped_file_is_in_package_data(name):
    """A data file not listed in package-data is missing from a wheel, and
    the feature then fails with "missing winocr.ps1".

    Read with `tomllib` rather than by slicing the text: the first attempt
    sliced on brackets and picked up the section HEADER as the list, which
    failed against a manifest that was correct.
    """
    import tomllib

    manifest = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = manifest["tool"]["setuptools"]["package-data"]["meow"]

    # Matched as GLOBS, because `desktop/*.ps1` is a correct entry for
    # `desktop/winocr.ps1` and a folder test would not see it.
    assert any(fnmatch.fnmatch(name, pattern) for pattern in patterns), (
        f"{name} is in SHIPPED and matches none of {patterns}")


@pytest.mark.parametrize("name", resources.SHIPPED)
def test_each_shipped_file_is_in_the_spec(name):
    """And a file not in the spec's `datas` is missing from the bundle."""
    spec = (ROOT / "meow.spec").read_text(encoding="utf-8")

    leaf = name.rsplit("/", 1)[-1]
    assert leaf in spec, f"{name} is in SHIPPED and not in meow.spec"


@pytest.mark.parametrize("folder", resources.SHIPPED_FOLDERS)
def test_each_shipped_folder_is_in_both(folder):
    import tomllib

    spec = (ROOT / "meow.spec").read_text(encoding="utf-8")
    manifest = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = manifest["tool"]["setuptools"]["package-data"]["meow"]

    assert folder in spec
    assert any(pattern.startswith(folder + "/") for pattern in patterns)


def test_the_cursor_lives_INSIDE_the_package_now():
    """It was `../../cat_cursor.png` - outside the package and in no
    manifest - so a pip install has never had a cat cursor. Nothing failed:
    `CatCursor` reports the file missing and the pointer stays normal, which
    is exactly the kind of absence nobody notices."""
    from meow.cat.cursor import SOURCE_IMAGE

    assert SOURCE_IMAGE.exists()
    assert "meow" in SOURCE_IMAGE.parts
    assert SOURCE_IMAGE.parent.name == "assets"


# --- the two processes ------------------------------------------------------

def test_the_window_is_started_one_way_from_everywhere():
    """The launcher and the agent dock both spawn it, and a bundle has no
    `window.py` - so the knowledge of how belongs in one place."""
    import inspect

    from meow.chat import launcher
    from meow.work import agentdock

    assert "how_to_start_the_window" in inspect.getsource(launcher)
    assert "how_to_start_the_window" in inspect.getsource(agentdock)


def test_frozen_starts_the_executable_with_a_subcommand(monkeypatch):
    import sys

    from meow.chat.launcher import WINDOW_COMMAND, how_to_start_the_window

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Meow\Meow.exe")

    assert how_to_start_the_window() == [r"C:\Meow\Meow.exe", WINDOW_COMMAND]


def test_from_source_it_hands_window_py_to_the_interpreter(monkeypatch):
    import sys

    from meow.chat.launcher import how_to_start_the_window

    monkeypatch.delattr(sys, "frozen", raising=False)
    command = how_to_start_the_window()

    assert command[1].endswith("window.py")


def test_the_subcommand_exists_and_is_not_advertised():
    """Nobody types `meow window`; the application does. It has to be in
    COMMANDS so the dispatcher routes it, and out of the usage text so it
    does not read as a thing to run."""
    from meow import cli

    assert "window" in cli.COMMANDS
    assert "meow window" not in (cli.__doc__ or "")


def test_the_search_for_a_live_window_matches_BOTH_spellings():
    """A machine can have one of each - a frozen copy in the tray and a
    source run under way - and matching one spelling spawned a second window
    on top of a live one. Thirty orphaned Qt processes is what that looked
    like."""
    import inspect

    from meow.chat import launcher

    source = inspect.getsource(launcher._window_already_running)

    assert "window.py" in source
    assert "* window" in source


# --- .env, wherever this install keeps it ----------------------------------

def test_the_kept_env_file_wins_so_an_update_cannot_take_the_keys(
        tmp_path, monkeypatch):
    """`%LOCALAPPDATA%/Meow/.env` is checked first either way: an update
    that overwrites the install folder must not take the keys with it."""
    import importlib

    kept = tmp_path / "Meow"
    kept.mkdir()
    (kept / ".env").write_text("OPENAI_API_KEY=x", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    from meow import config

    importlib.reload(config)
    try:
        assert config.ENV_PATH == kept / ".env"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_frozen_looks_beside_the_executable(tmp_path, monkeypatch):
    """Where somebody who unzipped the app can see and edit it."""
    import importlib
    import sys

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nothing-here"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "Meow.exe"))

    from meow import config

    importlib.reload(config)
    try:
        assert config.ENV_PATH == tmp_path / "app" / ".env"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_the_resource_root_follows_the_layout(monkeypatch, tmp_path):
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert resources.package_root() == tmp_path / "meow"
    assert resources.resource("assets", "cat_cursor.png") == (
        tmp_path / "meow" / "assets" / "cat_cursor.png")
