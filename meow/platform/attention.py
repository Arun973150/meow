"""What the user is doing that should change whether Meow draws or speaks.

One Win32 call, `SHQueryUserNotificationState`, answers two questions this
project has been getting wrong:

**CAN A MARK BE SEEN AT ALL?** A game in exclusive fullscreen owns the
display, so an overlay is not over it - it is behind it, or it forces the
game out of fullscreen. Meow would draw a ring, report that it had marked
the thing, and the user would see nothing: the worst failure shape this
project has, a confident claim about something that did not happen. And the
user's own Ctrl+Shift+M circle is invisible to them too, so the one signal
measured to rescue a hard grounding case is also gone.

**SHOULD IT SPEAK?** Presenting, or a full-screen application in the
foreground, is somebody who does not want a cat talking over them.

Read from HeyClicky's Windows build, which says it plainly to the model -
"a game in exclusive fullscreen, so nothing you draw, point at or highlight
can be seen over it... answer by voice" - and tells the user ONCE to switch
out of fullscreen rather than repeating it.
"""

from __future__ import annotations

import ctypes
from enum import IntEnum


class Attention(IntEnum):
    """`QUERY_USER_NOTIFICATION_STATE`, with the two that matter named.

    The numbers are Windows', not ours. `UNKNOWN` is zero so a failed call
    reads as "no reason to hold back", which is the right default: refusing
    to draw because a Win32 call failed would break the common case to
    protect the rare one.
    """

    UNKNOWN = 0
    NOT_PRESENT = 1              # no session, locked, or no display
    BUSY = 2                     # a full-screen application, not D3D
    RUNNING_D3D_FULL_SCREEN = 3  # a game that owns the display
    PRESENTATION_MODE = 4        # presenting: Windows itself stays quiet
    ACCEPTS_NOTIFICATIONS = 5    # the ordinary desktop
    QUIET_TIME = 6               # the first hour after an install
    RUNNING_WINDOWS_STORE_APP = 7


def attention() -> Attention:
    """What Windows thinks the user is in the middle of."""
    state = ctypes.c_int(0)
    try:
        shell = ctypes.windll.shell32
        # S_OK is 0. Any failure leaves `state` at zero, which is UNKNOWN.
        if shell.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return Attention.UNKNOWN
    except Exception:  # noqa: BLE001 - not knowing is a valid answer
        return Attention.UNKNOWN
    try:
        return Attention(state.value)
    except ValueError:
        return Attention.UNKNOWN


def marks_can_be_seen() -> bool:
    """Would a mark drawn right now be visible to the user?

    Only exclusive fullscreen is treated as no. A merely maximised window,
    a Store app and presentation mode all still composite an overlay above
    themselves - and refusing to draw over a presentation would break
    pointing at a slide, which is a thing somebody actually asks for.
    """
    return attention() is not Attention.RUNNING_D3D_FULL_SCREEN


def why_marks_cannot_be_seen() -> str:
    """One sentence for the model, or "" when there is nothing to say.

    Written for the model rather than the user, like every other tool
    reply: it has to be able to say this ONCE, in its own words, instead of
    claiming it marked something.
    """
    if marks_can_be_seen():
        return ""
    return ("NOTHING DRAWN ON SCREEN CAN BE SEEN RIGHT NOW: a game or "
            "application has the display in exclusive fullscreen, so a ring, "
            "an arrow or a highlight is behind it, and a circle the user "
            "draws with ctrl shift m does not show either. Your voice works "
            "and you can still see their screen, so answer in words. Say "
            "once, in one short sentence, that they would need to leave "
            "fullscreen for you to point at anything - then do not mention "
            "it again.")
