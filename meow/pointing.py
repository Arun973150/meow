"""Pointing at things - Phase 0.8.

The model says where something is by appending a tag to its reply, and a regex
pulls it out. That is the whole mechanism, and it is Clicky's - reproduced
faithfully here because it is the **control condition** for the ablation in
04-evaluation.md. Phase 1 replaces the guess with a UIA lookup; this is the
thing that has to be beaten.

    [POINT:x,y:label:screenN]        or        [POINT:none]

Coordinates are in IMAGE pixels, not screen pixels. The screenshot sent to the
model is downscaled, so every coordinate comes back in that smaller space and
has to be mapped through `ScreenShot.to_screen()`. Skipping that is how a
pointing system ends up consistently off by the downscale factor.

Moving the pointer is a real action taken on the user's machine, so two rules:

**It glides, it does not teleport.** A cursor that jumps has not shown the user
anything - they cannot follow it and do not learn where the thing was. Gliding
is the explanation.

**The user can take it back at any moment.** If the pointer moves by a hand
rather than by us, the glide stops immediately. Fighting a user for control of
their own mouse is unforgivable in software that is supposed to be assistive.
"""

from __future__ import annotations

import ctypes
import math
import re
import time
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)

# Clicky's regex, kept compatible. The label and screen parts are optional, and
# the tag is anchored to the end so a mention of the format inside a sentence
# is not mistaken for an instruction.
POINT_PATTERN = re.compile(
    r"\[POINT:(?:none|(\d+)\s*,\s*(\d+)"
    r"(?::([^\]:\s][^\]:]*?))?(?::screen(\d+))?)\]",
    re.IGNORECASE,
)

# How far the pointer may drift from where we put it before we conclude a human
# is driving. Mouse movement is integral, so anything above a couple of pixels
# is deliberate.
USER_TAKEOVER_PIXELS = 12


@dataclass(frozen=True)
class Point:
    """Where the model says something is, in IMAGE pixels."""

    x: int
    y: int
    label: str | None = None
    screen: int = 1

    def to_screen(self, shots) -> tuple[int, int] | None:
        """Map into virtual desktop coordinates using the matching screenshot.

        Returns None when the model names a screen that was never sent, which
        it does occasionally - answering about `screen2` on a single-monitor
        machine. Better to point at nothing than at the wrong place.
        """
        for shot in shots:
            if shot.label.lower() == f"screen{self.screen}".lower():
                return shot.to_screen(self.x, self.y)
        return shots[0].to_screen(self.x, self.y) if shots else None


def parse_point(text: str) -> Point | None:
    """Pull the coordinates out of a reply, if it has any."""
    match = POINT_PATTERN.search(text)
    if match is None or match.group(1) is None:
        return None  # no tag, or an explicit [POINT:none]
    return Point(
        x=int(match.group(1)),
        y=int(match.group(2)),
        label=(match.group(3) or "").strip() or None,
        screen=int(match.group(4) or 1),
    )


def strip_points(text: str) -> str:
    """Remove every tag, so the cat never reads coordinates out loud.

    Without this the user hears "open point colon four five zero comma three
    hundred", which is both absurd and a good way to fail a demo.
    """
    return POINT_PATTERN.sub("", text).strip()


def get_cursor() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def glide_to(target_x: int, target_y: int, seconds: float = 0.55,
             steps_per_second: int = 90) -> bool:
    """Move the pointer smoothly. Returns False if the user took over.

    Eased rather than linear - it starts slowly, covers the distance, and
    settles. A constant-speed slide reads as something being dragged; easing
    reads as something going somewhere on purpose.
    """
    start_x, start_y = get_cursor()
    distance = math.hypot(target_x - start_x, target_y - start_y)
    if distance < 2:
        return True

    # Long journeys take a little longer, but not proportionally - crossing two
    # monitors should not take four seconds.
    duration = min(seconds * 1.8, seconds * (0.5 + distance / 1400))
    total_steps = max(2, int(duration * steps_per_second))

    expected_x, expected_y = start_x, start_y
    for step in range(1, total_steps + 1):
        progress = step / total_steps
        eased = progress * progress * (3.0 - 2.0 * progress)  # smoothstep

        current_x, current_y = get_cursor()
        if (abs(current_x - expected_x) > USER_TAKEOVER_PIXELS
                or abs(current_y - expected_y) > USER_TAKEOVER_PIXELS):
            # The pointer is somewhere we did not put it, so a hand is on the
            # mouse. Stop instantly and leave it where they want it.
            return False

        expected_x = int(round(start_x + (target_x - start_x) * eased))
        expected_y = int(round(start_y + (target_y - start_y) * eased))
        user32.SetCursorPos(expected_x, expected_y)
        time.sleep(duration / total_steps)

    return True


def describe_point_protocol(shot) -> str:
    """The instruction block telling the model how to point.

    The image dimensions are stated explicitly because they ARE the coordinate
    space - the model is looking at a downscaled screenshot and has no idea
    what the real screen resolution is. Clicky does the same, and it is the
    reason its pointing works at all without a grounding model.
    """
    return (
        f"\n\nIf the user asks where something is, or to point at something, "
        f"end your reply with a tag on its own:\n"
        f"    [POINT:x,y:label:{shot.label}]\n"
        f"x and y are pixel coordinates in the image I attached, which is "
        f"{shot.image.width} wide and {shot.image.height} tall, with 0,0 at the "
        f"top left. Point at the CENTRE of the thing. Use a short label naming "
        f"what it is. If you cannot find it or the question is not about a "
        f"location, write [POINT:none] instead. Never mention the tag out loud "
        f"and never read the numbers - they are stripped before you are heard.\n"
        f"ALWAYS say a sentence before the tag, even a short one like "
        f"'it is over here'. The tag is removed before you are spoken, so a "
        f"reply that is only a tag comes out as silence - which is what "
        f"happened the first time this was tested."
    )
