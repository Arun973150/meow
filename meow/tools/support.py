"""Numbers and helpers the desktop tools need, and the harness too.

Below both on purpose. These lived in `harness.py`, and once the tools moved
out they needed them back - which would have made harness import tools and
tools import harness. Every value here was set by a measurement, and the
comments say which.
"""

from __future__ import annotations

from ..platform.monitors import get_virtual_desktop

# effect is not on screen the instant the call returns - a dialog takes a
# moment to appear, a window a moment to close - and checking too early reads
# the old world and reports a working click as unverified.
#
# A ceiling, not a duration: the wait ends as soon as anything actually
# changes, which is usually immediately. It was a flat sleep, so every action
# in every plan paid the worst case.
SETTLE_SECONDS = 0.35

# How long an application gets to put a window up and become walkable.
# Measured: Notepad appears after 389ms and is walkable after 993ms. This was
# a flat 1.6s sleep, so two thirds of a second went on nothing every time.
LAUNCH_SECONDS = 3.0

# Shortcuts that act on the WHOLE DESKTOP, and the control that does the same
# thing to one window. Asked to minimise VS Code, the model reached for win+m -
# which minimises everything the user has open, from a request about a single
# window. The button is right there in the digest and does exactly what was
# asked, so press_keys sends them back to it.
WHOLE_DESKTOP_SHORTCUTS = {
    "win+m": "Minimize",
    "win+d": "Minimize",
    "win+down": "Minimize",
    "win+up": "Maximize",
    "alt+f4": "Close",
}


# Injected on SHOW turns, where the job is to point rather than to describe.
# Without it the model answered "where is the file menu" out of its own
# memory - "top left, labelled File" - about a Notepad whose control list
# contains no File menu at all. A remembered layout is exactly what this
# project measured as wrong: the whole thesis is that the tree knows and the
# model does not.
def where_on_screen(target, monitor=None) -> str:
    """Where a control is, in the words a person would use.

    The model has the coordinates and no sense of them, so it invented the
    description: it pointed correctly at Minimize in the top right and said
    "at the bottom right corner of the window". The pointer went to the right
    place and the sentence sent the user to the wrong one, which is worse than
    saying nothing.

    Thirds rather than halves, because "middle" is a real and common answer
    and forcing everything into top/bottom makes a centred toolbar "top".
    """
    try:
        from .platform.monitors import get_virtual_desktop

        screen = monitor or get_virtual_desktop().primary
        width = max(1, screen.right - screen.left)
        height = max(1, screen.bottom - screen.top)
        x, y = target.centre if hasattr(target, "centre") else (
            (target.left + target.right) // 2, (target.top + target.bottom) // 2)
        across = (x - screen.left) / width
        down = (y - screen.top) / height
    except Exception:  # noqa: BLE001 - a description is not worth a crash
        return ""

    vertical = "top" if down < 0.33 else ("bottom" if down > 0.66 else "middle")
    horizontal = ("left" if across < 0.33
                  else ("right" if across > 0.66 else "centre"))
    if vertical == "middle" and horizontal == "centre":
        return "in the middle of the screen"
    if horizontal == "centre":
        return f"at the {vertical} of the screen"
    if vertical == "middle":
        return f"on the {horizontal} of the screen"
    return f"at the {vertical} {horizontal} of the screen"
