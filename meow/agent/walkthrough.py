"""Being taught something, one step at a time, with somebody watching.

Asking how to do a thing used to get the whole route read out in one breath -
"settings, then personalisation, then colours" - and then silence. That is a
recitation, not help. Somebody who says "i don't know how to change dark mode"
is telling you they cannot hold three steps and find them; reading four
faster is not the fix.

So a walkthrough says ONE step, waits, and watches. When the screen shows they
did it, it says the next one. It never repeats itself at them and it never
asks whether they managed - it looks.

**The signal that a step is done is the screen CHANGING with the next step on
it.** Both halves matter. Clicking "Personalization" opens a page containing
"Colors", so visibility alone is not proof of anything - on the first version
of this, reaching Personalization announced "that is it, colors, you are
there" while they were still a click away, because the page they had just
opened lists the one below it. Change plus visibility is proof; either alone
is not.

One step per change, for the same reason. Somebody who skips ahead is still
followed - if the current step has GONE and a later one is showing, they knew
part of the route and walked it, and sending them back to a step they have
already passed would be worse than saying nothing.

**Names are matched STRICTLY.** These steps came off a web page, and the rule
for web-derived names is exact or nothing - `find` degrades to word overlap,
under which everything exists. A walkthrough that claims they have arrived
because something vaguely similar is on screen is worse than one that waits.

Nothing here touches the screen, the clock or the network. It takes digests
and returns what to say, so the whole thing can be tested with a list of
control names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..desktop.lookup import strict_match
from ..desktop.uia import WindowDigest

# How many polls with nothing recognisable before the walkthrough says so. At
# the loop's polling rate this is a few seconds - long enough that glancing
# away does not trigger it, short enough to be useful when they are lost.
PATIENCE = 4


class Progress(Enum):
    ARRIVED = "arrived"      # the step is on screen: say it, point at it
    ADVANCED = "advanced"    # they did it; this is the next step
    FINISHED = "finished"    # the last step is on screen
    WAITING = "waiting"      # nothing has changed; say nothing
    LOST = "lost"            # nothing recognisable for a while


@dataclass
class Walkthrough:
    """A route, and how far along it somebody is."""

    steps: list[str]
    goal: str = ""
    index: int = 0
    started: bool = False
    finished: bool = False
    _unrecognised: int = field(default=0, repr=False)
    _last_seen: tuple = field(default=(), repr=False)

    @property
    def current(self) -> str:
        if 0 <= self.index < len(self.steps):
            return self.steps[self.index]
        return ""

    @property
    def remaining(self) -> int:
        return max(0, len(self.steps) - self.index)

    def _visible(self, name: str, digest: WindowDigest) -> bool:
        return strict_match(name, digest) is not None

    def observe(self, digest: WindowDigest | None) -> Progress:
        """Look at the screen and decide what, if anything, to say now."""
        if self.finished or not self.steps:
            return Progress.WAITING
        if digest is None:
            return Progress.WAITING

        signature = self._signature(digest)
        changed = signature != self._last_seen
        self._last_seen = signature

        here = self._visible(self.current, digest)
        following = (self.index + 1 < len(self.steps)
                     and self._visible(self.steps[self.index + 1], digest))

        if here or following:
            self._unrecognised = 0

        if not self.started and here:
            self.started = True
            return Progress.ARRIVED

        # Nothing moved, so nothing to say. Without this the walkthrough races
        # its own route: a Settings page LISTS the page below it, so
        # "Personalization" and "Colors" are both on screen the moment you
        # reach Personalization, and anything that advanced on visibility
        # alone announced "you are there" while they were still two clicks
        # away.
        if not changed:
            if not (here or following):
                self._unrecognised += 1
                if self._unrecognised == PATIENCE:
                    return Progress.LOST
            return Progress.WAITING

        # The screen changed. ONE step per change, which is what distinguishes
        # a page being open from a page being listed on the one above it.
        if following:
            self.index += 1
            if self.index == len(self.steps) - 1:
                self.finished = True
                return Progress.FINISHED
            return Progress.ADVANCED

        if here:
            if not self.started:
                self.started = True
                return Progress.ARRIVED
            return Progress.WAITING

        # The current step is GONE and something further along is showing:
        # they knew part of the route and skipped it. Follow them rather than
        # sending them back to a step they have already passed.
        for position in range(len(self.steps) - 1, self.index, -1):
            if self._visible(self.steps[position], digest):
                self.index = position
                self.started = True
                if position == len(self.steps) - 1:
                    self.finished = True
                    return Progress.FINISHED
                return Progress.ADVANCED

        self._unrecognised += 1
        if self._unrecognised == PATIENCE:
            # Once, on the threshold. Saying it every poll is nagging, and
            # somebody who has wandered off does not need a metronome.
            return Progress.LOST
        return Progress.WAITING

    @staticmethod
    def _signature(digest: WindowDigest) -> tuple:
        """Enough of the screen to tell "they clicked" from "they did not".

        Names rather than a count: a page can swap its contents without the
        number changing, and a count alone made a navigation look like
        stillness.
        """
        return (digest.app, digest.title,
                tuple(sorted(element.name for element in digest.elements[:60])))

    def say(self, progress: Progress) -> str:
        """What to speak for this progress, or "" for nothing.

        Written for the ear and deliberately short: one step is the whole
        point, and a sentence about a sentence is how a walkthrough turns back
        into a recitation.
        """
        if progress is Progress.ARRIVED:
            return f"start with {self.current.lower()}. i am pointing at it."
        if progress is Progress.ADVANCED:
            return f"good. now {self.current.lower()}."
        if progress is Progress.FINISHED:
            return f"that is it - {self.current.lower()}. you are there."
        if progress is Progress.LOST:
            first = self.current.lower()
            return f"i cannot see {first} from here. open it and i will carry on."
        return ""


def from_directions(directions, goal: str = "") -> Walkthrough | None:
    """A walkthrough from a mined route, or None if it is not worth one.

    One step is not a walkthrough - it is a sentence, and the ordinary SHOW
    reply already handles it better. Two or more is a sequence somebody can
    lose their place in, which is the thing this exists for.
    """
    if directions is None:
        return None
    steps = [step for step in getattr(directions, "steps", []) if step.strip()]
    if len(steps) < 2:
        return None
    return Walkthrough(steps=steps, goal=goal)
