# PyInstaller spec: Meow as one folder somebody can zip and send.
#
# `pyinstaller meow.spec` -> dist/Meow/Meow.exe
#
# ONE EXECUTABLE, TWO PROCESSES. The voice loop owns the main thread for a
# layered Win32 window at 60fps and Qt wants the main thread for its event
# loop, so the chat window has always been a second process. From a source
# tree that is `python meow/chat/window.py`; there is no .py file in a
# bundle, so `Meow.exe window` is the second process and
# `chat/launcher.py:how_to_start_the_window` is the one place that knows
# which. Building a second executable would double 300MB of Qt for nothing.
#
# ONE FOLDER, not one file. A --onefile build unpacks ~300MB to a temp
# directory on every launch, and this application starts a SECOND copy of
# itself for the window - so onefile would unpack twice per session and the
# two copies would not share `sys._MEIPASS`. A folder also lets somebody
# edit `.env` next to the exe, which is where `config._where_the_env_file_is`
# looks when frozen.
#
# WHAT GOES IN `datas` IS NOT A GUESS. `storage/resources.py:SHIPPED` is the
# list, `meow doctor` prints whatever is absent, and `tests/test_bundle.py`
# checks the three stay in step - which is the trap this project keeps
# finding: the recipes vanished when a path moved, and the cat cursor has
# been missing from every wheel ever built because it sat outside the
# package and nobody listed it.

from pathlib import Path

HERE = Path(SPECPATH)

# Shipped files, mirrored into `meow/...` inside the bundle so that
# `resources.package_root()` reads the same relative paths either way.
datas = [
    (str(HERE / "meow" / "assets" / "cat_cursor.png"), "meow/assets"),
    (str(HERE / "meow" / "desktop" / "winocr.ps1"), "meow/desktop"),
    (str(HERE / "meow" / "recipes"), "meow/recipes"),
]

# Imported through strings or lazily, so the analysis cannot see them.
hiddenimports = [
    # The chat window is reached by name from the `window` subcommand.
    "meow.chat.window",
    # uiautomation generates its COM wrapper at import time.
    "comtypes.gen",
    "comtypes.gen.UIAutomationClient",
    # Every tool module is imported by `meow/tools/__init__.py`'s ORDER
    # list, which is literal - but these pull in providers lazily.
    "langchain_openai",
    "langgraph.checkpoint.sqlite",
]

a = Analysis(
    ["meow/__main__.py"] if (HERE / "meow" / "__main__.py").exists()
    else ["meow_main.py"],
    pathex=[str(HERE)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # EXCLUDED, AND THIS IS THE DIFFERENCE BETWEEN A BUILD AND NO BUILD.
    # The first attempt ran for 23 minutes and produced nothing, and the log
    # said why: it was collecting torch, tensorflow, keras, pygame and pydub.
    # Meow imports none of them - they are simply installed in the same
    # site-packages, and a hook for one of the langchain packages collects
    # whatever it can find. Analysis follows the graph, not the code that is
    # actually reachable from this app.
    #
    # Anything added here must be something `meow` genuinely never imports.
    # The check is `grep -rl "^import X" meow/`, not a guess.
    excludes=[
        # test and build tooling
        "pytest", "PyInstaller", "setuptools", "pip",
        # a GUI toolkit this app does not use (it uses PySide6)
        "tkinter",
        # the data-science stack, none of which `meow` imports
        "torch", "tensorflow", "keras", "jax", "sklearn", "scipy",
        "matplotlib", "pandas", "transformers", "numexpr", "numpy.testing",
        # audio libraries that are not the one in use (sounddevice is)
        "pygame", "pydub",
        # langchain's optional integrations, which is where the collection
        # of everything above starts
        "langchain_community",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Meow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A CONSOLE, deliberately, for now. This application prints its whole
    # turn - the route, the latency, what it said - and that log is how
    # every bug in this project has been found. A windowed build hides it
    # and also breaks `print` to a closed stdout. The console goes when
    # there is somewhere else for the log to land.
    console=True,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Meow",
)
