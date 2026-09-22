"""One command, with subcommands.

    meow                 run the companion
    meow doctor          what is installed, which keys are set
    meow connectors      which services are connected, and connect one
    meow stress          64 edge cases across every module
    meow smoke           start the real app and fail on a traceback
    meow label           hand-mark targets for the held-out evaluation
    meow evaluate        the UIA / vision ablation
    meow evaluate --labelled   score against the hand-marked set

A desktop application ships one executable. `doctor` and `stress` are things
you ask it about itself, not separate programs, and a folder of loose scripts
is not something you can `pip install`.

Bare `meow` runs the companion, so the common case stays one word.
"""

from __future__ import annotations

import sys

# `meow ...` with no recognised subcommand runs the companion and passes
# everything through, so `meow --mute` keeps working without being listed.
COMMANDS = ("doctor", "connectors", "stress", "smoke", "evaluate", "label",
            "help")


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

    if command == "label":
        from .diagnostics import label

        raise SystemExit(label.main())

    if command == "evaluate":
        if "--labelled" in arguments or "--labeled" in arguments:
            from .diagnostics import held_out, label

            labels = label.load()
            if not labels:
                print()
                print("  No hand-labelled targets yet. Run:  meow label")
                print()
                raise SystemExit(1)
            print()
            print(f"  replaying {len(labels)} hand-labelled targets...")
            print(held_out.run(labels).summary())
            print()
            return

        from .diagnostics import evaluate

        evaluate.main()
        return

    from .app import loop

    loop.main()


if __name__ == "__main__":
    main()
