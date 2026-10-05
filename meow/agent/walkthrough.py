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

**It is PAUSED by a sentence, never destroyed by one.** The loop used to
cancel the walkthrough before routing anything, on the reasoning that whatever
they just said, they are no longer following the old route. That is true of
"open notepad" and false of everything somebody actually says mid-route: "ok
what next" destroyed the thing that knew what next was, "i can't find it"
destroyed the thing that could point at it, and a cough the transcriber heard
as a word destroyed both. The goal and every step already walked went with it.

So a walkthrough holds its goal and its completed steps across a pause, and
answers four things locally without a model: carry on, say that again, i
cannot find it, and stop.

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

# A budget, not a length. A mined route is normally two to four steps, and
# anything claiming thirty is a page that was read wrongly rather than a
# genuinely long procedure. Cut rather than refused: fourteen steps of real
# help beats none, and `truncated` makes the last one say so instead of
# pretending it was the end.
MAX_STEPS = 15


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
    # How a step is known to be done. Two kinds, and the difference is where
    # the steps came from.
    #
    # NAMED steps are places - "Personalization", "Colors" - mined from a
    # route or written in a recipe, and a step is done when that name appears
    # on screen. That is exact and free, and it only works where the
    # accessibility tree can see.
    #
    # DOING steps are instructions - "press shift a and choose a UV sphere",
    # "move to a later frame" - which the model knows and no tree contains.
    # There is no name to look for, so a step is done when the screen CHANGED
    # and they said so. Without this, teaching anything outside Windows
    # Settings had to be recited in one breath, which is the thing a
    # walkthrough exists to replace.
    doing: bool = False
    index: int = 0
    started: bool = False
    finished: bool = False
    truncated: bool = False
    # Every step they have actually walked, in order. Kept because this is
    # what has to survive a pause: resuming with the index alone can say
    # "now colors" without being able to say what came before it, and a
    # walkthrough that cannot recount the route cannot be resumed out loud.
    walked: list[str] = field(default_factory=list)
    paused: bool = False
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

    # --- interrupted, and picked back up ---------------------------------

    def pause(self) -> None:
        """They said something. Stop watching; remember everything."""
        self.paused = True

    def resume(self) -> str:
        """Pick the route back up. Returns what to say, which is never "".

        Deliberately NOT the same sentence as `say(ARRIVED)`. Somebody who
        just asked what is next has not forgotten they are being taught, so
        being told to "start with" a step they are halfway through reads as
        the walkthrough having lost its place - which is the exact failure
        this method exists to prevent.
        """
        self.paused = False
        self.started = True
        if self.finished or not self.current:
            return ""
        if self.index == len(self.steps) - 1:
            return f"last one - {self.current.lower()}."
        return f"{self.current.lower()} is next."

    def recount(self) -> str:
        """The route so far, for when they ask where they had got to."""
        if not self.walked:
            return ""
        if len(self.walked) == 1:
            return self.walked[0].lower()
        return (", ".join(step.lower() for step in self.walked[:-1])
                + " then " + self.walked[-1].lower())

    def _record(self) -> None:
        """Remember the step being left behind, without repeating it."""
        step = self.current
        if step and (not self.walked or self.walked[-1] != step):
            self.walked.append(step)

    def _visible(self, name: str, digest: WindowDigest) -> bool:
        return strict_match(name, digest) is not None

    def advance(self) -> Progress:
        """They said they did it. Move on.

        For DOING steps, which have no name to watch for. The watcher cannot
        tell "pressed shift A and added a sphere" from "sat still", so the
        person saying so is the signal - and believing them is right for the
        same reason the watcher believes "done" on a named route: somebody
        volunteering that they have finished is not asking to be checked up
        on.
        """
        if self.finished or not self.steps:
            return Progress.WAITING
        self._record()
        if self.index >= len(self.steps) - 1:
            self.finished = True
            return Progress.FINISHED
        self.index += 1
        self.started = True
        return Progress.ADVANCED

    def observe(self, digest: WindowDigest | None) -> Progress:
        """Look at the screen and decide what, if anything, to say now."""
        if self.finished or not self.steps:
            return Progress.WAITING
        if self.doing:
            # Nothing to watch for. A step like "press I and choose Location"
            # leaves no name on screen, and announcing progress from a window
            # title changing would be guessing out loud. It waits to be told.
            return Progress.WAITING
        if self.paused:
            # The watcher thread is stopped across a pause, so this is
            # belt and braces - but a walkthrough that narrated while the
            # user was mid-sentence would talk over them.
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
            self._record()
            self.index += 1
            if self.index == len(self.steps) - 1:
                self.finished = True
                self._record()
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
                # Everything between here and there was walked, whether or
                # not it was ever announced - they knew the route and used
                # it, and `recount` has to be able to say so.
                for passed in range(self.index, position):
                    step = self.steps[passed]
                    if not self.walked or self.walked[-1] != step:
                        self.walked.append(step)
                self.index = position
                self.started = True
                if position == len(self.steps) - 1:
                    self.finished = True
                    self._record()
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
            if self.doing:
                # No "i am pointing at it": a doing step is an instruction,
                # and claiming to point at one is a promise about the screen
                # that nothing here checked.
                return f"{self.current.lower()} tell me when you have."
            return f"start with {self.current.lower()}. i am pointing at it."
        if progress is Progress.ADVANCED:
            if self.doing:
                return f"good. now {self.current.lower()}"
            return f"good. now {self.current.lower()}."
        if progress is Progress.FINISHED:
            if self.truncated:
                # Never claim the end of a route that was cut. "You are
                # there" about step fifteen of twenty is a lie the user only
                # discovers by not being there.
                return (f"{self.current.lower()} - that is as far as the "
                        f"instructions i found go.")
            return f"that is it - {self.current.lower()}. you are there."
        if progress is Progress.LOST:
            first = self.current.lower()
            return f"i cannot see {first} from here. open it and i will carry on."
        return ""

    # --- answering them directly, with no model ---------------------------

    def said_again(self) -> str:
        """They asked to hear the step again.

        The class docstring says it never repeats itself at them, and that
        still holds: repeating UNPROMPTED is nagging, repeating when asked is
        the whole job. Worded differently on purpose, because hearing the
        identical sentence back is how a person concludes they are talking to
        a recording.
        """
        if not self.current:
            return ""
        return f"you are looking for {self.current.lower()}."

    def help_me_find_it(self) -> str:
        """They cannot see the step. Said alongside a point at it."""
        if not self.current:
            return ""
        return f"{self.current.lower()} - i am pointing at it now."

    def stopped(self) -> str:
        """They ended it. Says what was covered, so it was not for nothing."""
        self.finished = True
        route = self.recount()
        if route:
            return f"alright, stopping there. you got as far as {route}."
        return "alright, stopping there."


def from_directions(directions, goal: str = "") -> Walkthrough | None:
    """A walkthrough from a mined route, or None if it is not worth one.

    One step is not a walkthrough - it is a sentence, and the ordinary SHOW
    reply already handles it better. Two or more is a sequence somebody can
    lose their place in, which is the thing this exists for.
    """
    if directions is None:
        return None
    steps = [step for step in getattr(directions, "steps", []) if step.strip()]
    return from_steps(steps, goal)


def from_steps(steps, goal: str = "", doing: bool = False):
    """A walkthrough from plain steps, or None if it is not worth one.

    One step is not a walkthrough - it is a sentence, and the ordinary reply
    already handles it better. Two or more is a sequence somebody can lose
    their place in, which is the thing this exists for.
    """
    kept = [str(step).strip() for step in steps if str(step).strip()]
    if len(kept) < 2:
        return None
    return Walkthrough(steps=kept[:MAX_STEPS], goal=goal, doing=doing,
                       truncated=len(kept) > MAX_STEPS)
