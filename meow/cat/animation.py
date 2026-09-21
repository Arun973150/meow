"""Animation states.

Seven states, each a function from elapsed time to a CatPose. Because a pose is
just numbers, switching states CROSS-FADES rather than cuts - the ears rise into
listening instead of snapping there. That matters more than it sounds: an
assistant that changes expression abruptly reads as glitchy, and one that eases
reads as alive.

The states are the user's only feedback about what Meow is doing while it is not
speaking. Someone who cannot easily read a status bar still knows the difference
between a cat that is listening and a cat that is working, so these have to stay
distinguishable at a glance and at small sizes. With line art there is no colour
or posture to lean on, so the separation has to come from the eyes and ears.
"""

from __future__ import annotations

import math
import random
from dataclasses import fields, replace
from enum import Enum

from .sprite import CatPose


class CatState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    POINTING = "pointing"
    WORKING = "working"
    SLEEPING = "sleeping"


# Long enough to read as motion, short enough that the cat feels responsive when
# the user starts talking. Below ~120ms it looks like a cut.
TRANSITION_SECONDS = 0.22


def blend_poses(start: CatPose, end: CatPose, amount: float) -> CatPose:
    """Interpolate every field. Works because a pose is all floats."""
    eased = amount * amount * (3.0 - 2.0 * amount)  # smoothstep
    values = {}
    for pose_field in fields(CatPose):
        first = getattr(start, pose_field.name)
        second = getattr(end, pose_field.name)
        values[pose_field.name] = first + (second - first) * eased
    return CatPose(**values)


class Animator:
    """Holds the current state and produces a pose for any moment in time."""

    def __init__(self, state: CatState = CatState.IDLE,
                 seed: int | None = None) -> None:
        self.state = state
        self._previous_state = state
        self._transition_started_at: float | None = None
        self._random = random.Random(seed)
        self._next_blink_at = 2.0
        self._blink_started_at: float | None = None

        # Set by the voice layer while speaking, 0..1. Driving the mouth from
        # real audio amplitude is what stops it looking like a metronome.
        self.speech_level = 0.0
        # Where to point, in sprite-relative terms, -1..1 on each axis.
        self.point_direction_x = 0.0
        self.point_direction_y = 0.0
        # Which way the cat is currently travelling, -1..1, set by the follower
        # while it chases the cursor. Applied on top of whatever the state
        # produces, so no state has to know the cat is moving.
        self.travel_x = 0.0
        self.travel_y = 0.0

    def set_state(self, state: CatState, now: float) -> None:
        if state is self.state:
            return
        self._previous_state = self.state
        self.state = state
        self._transition_started_at = now

    def pose_at(self, now: float) -> CatPose:
        target = self._pose_for(self.state, now)
        if self._transition_started_at is None:
            return self._apply_travel(target)

        elapsed = now - self._transition_started_at
        if elapsed >= TRANSITION_SECONDS:
            self._transition_started_at = None
            return target

        previous = self._pose_for(self._previous_state, now)
        return self._apply_travel(
            blend_poses(previous, target, elapsed / TRANSITION_SECONDS)
        )

    def _apply_travel(self, pose: CatPose) -> CatPose:
        """Lean and look into the direction of motion.

        A sprite that slides across the screen while facing dead ahead reads as
        an icon being dragged. Leaning into the movement is what makes it read
        as something going somewhere.

        Added to whatever the state already asked for rather than replacing it,
        so pointing still aims at its target while the cat travels.
        """
        if abs(self.travel_x) < 0.01 and abs(self.travel_y) < 0.01:
            return pose
        return replace(
            pose,
            lean_x=max(-1.0, min(1.0, pose.lean_x + self.travel_x * 0.55)),
            look_x=max(-1.0, min(1.0, pose.look_x + self.travel_x * 0.45)),
            look_y=max(-1.0, min(1.0, pose.look_y + self.travel_y * 0.35)),
            head_tilt=pose.head_tilt + self.travel_x * 6.0,
        )

    # --- per-state poses -------------------------------------------------

    def _pose_for(self, state: CatState, now: float) -> CatPose:
        if state is CatState.IDLE:
            return self._idle(now)
        if state is CatState.LISTENING:
            return self._listening(now)
        if state is CatState.THINKING:
            return self._thinking(now)
        if state is CatState.SPEAKING:
            return self._speaking(now)
        if state is CatState.POINTING:
            return self._pointing(now)
        if state is CatState.WORKING:
            return self._working(now)
        return self._sleeping(now)

    def _idle(self, now: float) -> CatPose:
        # The reference drawing itself: content, closed, curved eyes.
        return CatPose(
            ear_perk=0.88,
            eye_curve=1.0,
            eye_dot=0.0,
            body_bob=0.55 * math.sin(now * 1.15),  # breathing
            head_tilt=2.5 * math.sin(now * 0.37),
        )

    def _listening(self, now: float) -> CatPose:
        # Eyes snap open and the ears go all the way up. This is the single
        # most important state to read instantly - it is the one that tells the
        # user their voice is being heard.
        return CatPose(
            ear_perk=1.0,
            eye_curve=0.15,
            eye_dot=self._blink(now, interval=(4.5, 8.0)),
            # Wide open and rarely blinking. A listening cat stares.
            look_y=-0.15,
            whisker_lift=1.0,
            body_bob=0.30 * math.sin(now * 1.9),
            lean_x=0.12,
        )

    def _thinking(self, now: float) -> CatPose:
        return CatPose(
            ear_perk=0.60,
            eye_curve=0.2,
            eye_dot=self._blink(now),
            # Eyes up and to the side: the universal shorthand for thinking.
            look_x=0.60 + 0.25 * math.sin(now * 0.9),
            look_y=-0.70,
            whisker_lift=0.3,
            body_bob=0.35 * math.sin(now * 1.0),
            head_tilt=7.0,
        )

    def _speaking(self, now: float) -> CatPose:
        # Prefer real audio amplitude. The sine fallback exists only so the cat
        # still animates when nothing has set speech_level yet.
        if self.speech_level > 0.0:
            mouth = min(1.0, self.speech_level)
        else:
            mouth = 0.5 + 0.5 * math.sin(now * 11.0)
        return CatPose(
            ear_perk=0.92,
            eye_curve=1.0,
            eye_dot=0.0,
            mouth_open=mouth,
            whisker_lift=0.5,
            body_bob=0.45 * math.sin(now * 1.3),
        )

    def _pointing(self, now: float) -> CatPose:
        # Look, lean and tilt the same way, so the gesture reads from across the
        # room. There is no paw in this drawing to raise, so direction has to
        # carry the whole meaning.
        return CatPose(
            ear_perk=1.0,
            eye_curve=0.0,
            eye_dot=1.0,
            look_x=self.point_direction_x,
            look_y=self.point_direction_y,
            whisker_lift=0.8,
            body_bob=0.20 * math.sin(now * 2.2),
            lean_x=0.9 * self.point_direction_x,
            head_tilt=10.0 * self.point_direction_x,
        )

    def _working(self, now: float) -> CatPose:
        # Flat, narrowed eyes and lowered ears. An earlier version was eyes-down
        # and little else, which read as identical to idle in the preview sheet.
        # If the user cannot tell at a glance whether Meow is working or
        # waiting, the state is not doing its job.
        return CatPose(
            ear_perk=0.40,
            # Flat line eyes, not round ones. A narrowed squint is the clearest
            # "concentrating" signal available in line art, and it is the only
            # state that uses eye_curve = 0.
            eye_curve=0.0,
            eye_dot=0.0,
            whisker_lift=-0.2,
            body_bob=0.30 * math.sin(now * 1.05),
            head_tilt=-8.0,
        )

    def _sleeping(self, now: float) -> CatPose:
        return CatPose(
            ear_perk=0.12,
            # Curving the other way from idle: the difference between content
            # and asleep is the direction of the same two strokes.
            eye_curve=-1.0,
            eye_dot=0.0,
            whisker_lift=-1.0,
            # Slower and deeper than idle breathing.
            body_bob=1.0 * math.sin(now * 0.55),
            zzz=1.0,
        )

    # --- blinking --------------------------------------------------------

    def _blink(self, now: float, interval: tuple[float, float] = (2.5, 6.0)) -> float:
        """How open the round eyes are: 1.0 normally, dipping to 0 for a blink.

        Multiplies eye_dot rather than replacing the eyes, so a blink fades the
        round eye out and the line eye underneath shows through. That is the
        whole blink - no extra shape needed.

        Randomly timed rather than periodic. A cat that blinks on a fixed
        metronome looks mechanical in a way that is hard to place but easy to
        notice.
        """
        blink_duration = 0.16

        if self._blink_started_at is not None:
            elapsed = now - self._blink_started_at
            if elapsed >= blink_duration:
                self._blink_started_at = None
                self._next_blink_at = now + self._random.uniform(*interval)
            else:
                half = blink_duration / 2.0
                if elapsed < half:
                    return 1.0 - (elapsed / half)
                return (elapsed - half) / half

        if now >= self._next_blink_at:
            self._blink_started_at = now

        return 1.0
