"""Following the cursor.

When Meow is activated the cat leaves its corner and trails the pointer, so the
thing you are talking to is near the thing you are talking about. When it is
dismissed it goes home again.

Two rules shape all of this:

**It trails, it does not stick.** A sprite locked to the cursor is a second
cursor, and it covers the thing the user is pointing at. The cat lags behind on
a spring, sits off to one side, and flips to the other side near a screen edge.

**It never lands on the cursor hotspot.** Meow clicks things. A cat sitting
exactly where the click goes would be in its own screenshots of the target - and
would make it impossible for the user to see what is about to be clicked.

The spring is critically damped: it eases in and stops, with no overshoot and no
wobble. Overshoot on a pointer-following sprite reads as the cat being drunk.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FollowSettings:
    # Where the cat sits relative to the cursor, in pixels. Down and to the
    # right by default, the way a pet walks beside you rather than in front.
    offset_x: int = 34
    offset_y: int = 26

    # Higher is snappier. Tuned so the cat arrives a beat after the cursor
    # stops, which is what makes it read as following rather than as being
    # dragged.
    stiffness: float = 78.0

    # Pixels per second. A cat that crosses a 4K desktop instantly does not
    # look like it moved, it looks like it teleported.
    max_speed: float = 3200.0

    # Below this distance the cat stops correcting. Without it, the spring
    # jitters by a pixel forever and the sprite shimmers while idle.
    settle_distance: float = 0.6

    # A frame hitch must not be integrated as one huge timestep or the spring
    # explodes and the cat is flung off screen. Anything longer is treated as
    # this long.
    max_timestep: float = 0.05


class CursorFollower:
    """Eases a sprite toward a moving target."""

    def __init__(self, x: float, y: float,
                 settings: FollowSettings | None = None) -> None:
        self.settings = settings or FollowSettings()
        self.x = float(x)
        self.y = float(y)
        self.velocity_x = 0.0
        self.velocity_y = 0.0

    @property
    def speed(self) -> float:
        return math.hypot(self.velocity_x, self.velocity_y)

    @property
    def travel_direction_x(self) -> float:
        """Horizontal heading, -1..1, for leaning into the motion.

        Normalised against a speed well below max_speed, so an ordinary
        cursor move produces a full lean rather than only a sprint doing so.
        """
        return max(-1.0, min(1.0, self.velocity_x / 900.0))

    @property
    def travel_direction_y(self) -> float:
        return max(-1.0, min(1.0, self.velocity_y / 900.0))

    def update(self, target_x: float, target_y: float, timestep: float) -> None:
        settings = self.settings
        timestep = min(timestep, settings.max_timestep)
        if timestep <= 0.0:
            return

        # Critical damping is exactly 2*sqrt(stiffness) - the boundary where the
        # spring stops as fast as possible without crossing the target.
        damping = 2.0 * math.sqrt(settings.stiffness)

        for axis in ("x", "y"):
            position = getattr(self, axis)
            velocity = getattr(self, f"velocity_{axis}")
            target = target_x if axis == "x" else target_y

            acceleration = (target - position) * settings.stiffness - velocity * damping
            velocity += acceleration * timestep
            position += velocity * timestep

            setattr(self, axis, position)
            setattr(self, f"velocity_{axis}", velocity)

        speed = self.speed
        if speed > settings.max_speed:
            scale = settings.max_speed / speed
            self.velocity_x *= scale
            self.velocity_y *= scale

        if (abs(target_x - self.x) < settings.settle_distance
                and abs(target_y - self.y) < settings.settle_distance
                and speed < 8.0):
            self.x, self.y = target_x, target_y
            self.velocity_x = self.velocity_y = 0.0

    def snap_to(self, x: float, y: float) -> None:
        """Jump without easing. Used when the cat is first shown."""
        self.x, self.y = float(x), float(y)
        self.velocity_x = self.velocity_y = 0.0


def target_beside_cursor(
    cursor_x: int,
    cursor_y: int,
    sprite_width: int,
    sprite_height: int,
    monitor,
    settings: FollowSettings,
) -> tuple[int, int]:
    """Where the sprite wants to be, given where the cursor is.

    Prefers down-and-right of the pointer, and mirrors to the left when that
    would push it past the edge of the monitor. Mirroring rather than clamping
    matters: a clamped cat piles up in the corner and ends up under the cursor,
    which is the one place it must never be.

    `monitor` is the display the cursor is currently on, so the cat stays on
    that screen rather than being pulled toward the primary one.
    """
    left = cursor_x + settings.offset_x
    if left + sprite_width > monitor.work_right:
        left = cursor_x - settings.offset_x - sprite_width

    top = cursor_y + settings.offset_y
    if top + sprite_height > monitor.work_bottom:
        top = cursor_y - settings.offset_y - sprite_height

    # Still clamp as a backstop, for a cursor in a corner of a small monitor
    # where neither side fits.
    left = max(monitor.work_left, min(left, monitor.work_right - sprite_width))
    top = max(monitor.work_top, min(top, monitor.work_bottom - sprite_height))
    return int(left), int(top)
