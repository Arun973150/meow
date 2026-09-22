"""One command, with subcommands.

    meow                 run the companion
    meow doctor          what is installed, which keys are set
    meow connectors      which services are connected, and connect one
    meow stress          64 edge cases across every module
    meow smoke           start the real app and fail on a traceback
    meow evaluate        the UIA / vision ablation

A desktop application ships one executable. `doctor` and `stress` are things
you ask it about itself, not separate programs, and a folder of loose scripts
is not something you can `pip install`.

Bare `meow` runs the companion, so the common case stays one word.
"""

from __future__ import annotations

import sys

# `meow ...` with no recognised subcommand runs the companion and passes
# everything through, so `meow --mute` keeps working without being listed.
COMMANDS = ("doctor", "connectors", "stress", "smoke", "evaluate", "help")


def _usage() -> None:
    print(__doc__)


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = arguments[0] if arguments and arguments[0] in COMMANDS else ""

    if command:
        # Consumed here so each subcommand sees its own flags at argv[1:],
        # exactly as it would if it were still a standalone script.
        sys.argv = [f"meow {command}", *arguments[1:]]

    if command == "help":
        _usage()
        return

    if command == "doctor":
        from .diagnostics import keys

        keys.main()
        return

    if command == "connectors":
        from .diagnostics import connectors

        connectors.main()
        return

    if command == "stress":
        from .testing import stress

        raise SystemExit(stress.main())

    if command == "smoke":
        from .testing import smoke

        raise SystemExit(smoke.main())

    if command == "evaluate":
        from .diagnostics import evaluate

        evaluate.main()
        return

    from .app import loop

    loop.main()


if __name__ == "__main__":
    main()
