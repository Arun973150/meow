"""`python -m meow`, and the entry point a frozen bundle starts at.

`pip install -e .` creates a `meow` command from `[project.scripts]`, which
is the normal way in. A bundle has no console script, so PyInstaller needs a
module to point at - and `python -m meow` being the same door means the
bundle runs the same code path as the development machine rather than a
second one written for it.
"""

from __future__ import annotations

import multiprocessing

from .cli import main

if __name__ == "__main__":
    # BEFORE ANYTHING ELSE, and only meaningful in a frozen build. Without
    # it a bundled app that starts a child process re-runs its own startup
    # in the child instead - which for this application means a second
    # voice loop fighting for the hotkey. Meow spawns its chat window with
    # `subprocess` rather than `multiprocessing`, so nothing is known to
    # depend on this; it is one line and the failure it prevents is a
    # process storm, so it goes in anyway.
    multiprocessing.freeze_support()
    main()
