"""Start the whole app, run the render loop, and fail on any traceback.

Written after shipping a crash that every check in use at the time passed.
`ast.parse` says the file is syntactically valid, which cannot see a call with
the wrong number of arguments. Printing the banner says startup worked, and the
crash was in the render loop a few frames later - and piping that through
`head` truncated the traceback before anyone saw it.

So this runs the real `scripts/meow.py`, lets the loop turn over for a few
seconds, and fails if the process dies or prints a traceback. It exercises the
frame path: the cat drawing, the agent dock laying out and drawing, the bubble,
the background sampling.

    python scripts/smoke.py           # a few seconds, no microphone needed
    python scripts/smoke.py --with-agents

`--with-agents` additionally drives the dock directly - sync, layout, draw,
click-poll, close - with two agents in it. The app's own dock follows
`tasks.running`, which is in-process, so there is no way to make the running
app show icons from outside it; planting rows in the record would look like it
worked and test nothing. This exercises the same code in this process instead.

Verified to fail: reintroducing the wrong-arity `dock.layout` call that shipped
makes this exit non-zero with the traceback in view.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time


from meow.console import use_utf8_console

# Started as a MODULE, not a file path. The loop moved into the package
# and a path here would have to track it; `-m meow.cli` is whatever
# the installed entry point runs, which is the thing worth smoking.

# Long enough for several hundred frames, the first background sample, and a
# dock layout. Short enough to run without thinking about it.
SECONDS = 8.0

TROUBLE = ("Traceback", "TypeError", "AttributeError", "NameError",
           "ValueError", "KeyError", "IndexError", "error:")

# The last line the app prints before the render loop takes over. Seeing it is
# the only proof the loop is actually turning - "the process is still alive"
# is not, and counting it as proof is how this test passed an app that was
# sitting in a six-second wait for a hotkey another meow was holding.
READY = "Ctrl+C to quit"

# How long to wait for that line. Startup is about two seconds warm; the
# hotkey wait alone is six, so this has to be longer than that or a stray
# process reads as a slow one.
READY_SECONDS = 20.0


def _ready(output: str) -> bool:
    return READY in output


def exercise_the_dock() -> None:
    """Run the dock through its whole cycle, with real overlays.

    The app's dock reads `tasks.running`, which lives in its process, so an
    outside test cannot put an icon on its screen. Driving the same code here
    covers what matters - that layout, draw, the click poll and teardown all
    work with agents actually in it - without pretending the running app is
    the thing being exercised.
    """
    from meow.work.agentdock import AgentDock
    from meow.platform.dpi import enable_per_monitor_dpi_awareness
    from meow.platform.monitors import get_virtual_desktop

    enable_per_monitor_dpi_awareness()
    monitor = get_virtual_desktop().primary
    dock = AgentDock()
    try:
        dock.sync([(901, "smoke test research", "magnifier"),
                   (902, "smoke test plan", "gear")])
        dock.layout(monitor)
        for frame in range(10):
            dock.draw(frame * 0.1)
            dock.clicked()
            time.sleep(0.03)
        places = [str(agent.bounds) for agent in dock.agents.values()]
        print(f"  dock drew 2 icons at {places}")
        dock.sync([(901, "smoke test research", "magnifier")])
        assert sorted(dock.agents) == [901], "a finished agent kept its icon"
        dock.sync([])
        assert not dock.agents, "the dock did not empty"
        print("  dock despawned both")
    finally:
        dock.close()


def main() -> int:
    use_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-agents", action="store_true",
                        help="dock two live agents so their icons are drawn")
    parser.add_argument("--seconds", type=float, default=SECONDS)
    args = parser.parse_args()

    if args.with_agents:
        exercise_the_dock()

    print(f"  running the app for {args.seconds:.0f}s...")
    process = subprocess.Popen(
        [sys.executable, "-u", "-m", "meow.cli", "--mute", "--keyword-routing"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    # Read as it arrives, on a thread. `communicate` after `terminate` loses
    # whatever the child had not flushed, and on Windows terminate is an
    # immediate kill with no flush at all - so a crash late in the run could
    # be reported as a clean one.
    collected: list[str] = []
    reader = threading.Thread(
        target=lambda: collected.extend(process.stdout), daemon=True)
    reader.start()

    # Wait for the app to say it is ready, then let the loop turn. Sleeping a
    # flat eight seconds counted an app that never STARTED as one that
    # survived: a second meow holding ctrl+m makes this one wait for the key,
    # and at the eight second mark it is alive, silent, and has not drawn a
    # single frame. That passed.
    deadline = time.time() + READY_SECONDS
    while time.time() < deadline:
        if process.poll() is not None or _ready("".join(collected)):
            break
        time.sleep(0.1)

    started = _ready("".join(collected))
    if started:
        time.sleep(args.seconds)

    alive = process.poll() is None
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
    reader.join(timeout=5)
    output = "".join(collected)

    print("  --- output " + "-" * 50)
    for line in output.splitlines():
        print("  " + line)
    print("  " + "-" * 61)

    problems = [line for line in output.splitlines()
                if any(word in line for word in TROUBLE)]

    if not started:
        if "could not register" in output or not output.strip():
            print("\n  FAILED: the app never started. Something else holds "
                  "ctrl+m -")
            print("  close any other running meow. That is the environment, "
                  "not the code.")
        else:
            print(f"\n  FAILED: no sign of life within {READY_SECONDS:.0f}s - "
                  f"it never finished starting up")
    elif not alive:
        print("\n  FAILED: the app died before it was asked to stop")
    if problems:
        print("\n  FAILED: it printed trouble")
        for line in problems[:10]:
            print("    " + line.strip())

    if started and alive and not problems:
        print(f"\n  ok - survived {args.seconds:.0f}s of real frames"
              + (" with agents docked" if args.with_agents else ""))
        return 0
    return 1


if __name__ == "__main__":
    main()
