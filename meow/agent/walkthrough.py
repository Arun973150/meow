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

**A LONG lesson has PARTS.** "Teach me how to design a full environment in
blender" is thirty-odd steps, and the two things available without parts are
both bad: compress it into eight vague ones ("model the terrain") that teach
nobody anything, or read out a flat list of thirty on which step nineteen
tells somebody nothing about whether they are nearly done. `stage_of` names
the part each step belongs to, so each step arrives inside something with a
name and an end - "that is the terrain done, now the lighting" - and the model
can answer how much is left without being told again. It is the same argument
as one step at a time, one level up.

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

# A budget, not a length. A MINED route is normally two to four steps, and
# anything claiming thirty is a page that was read wrongly rather than a
# genuinely long procedure. Cut rather than refused: fourteen steps of real
# help beats none, and `truncated` makes the last one say so instead of
# pretending it was the end.
MAX_STEPS = 15

# And a different budget for a procedure the MODEL wrote, because the reason
# for the one above does not apply to it. Fifteen was chosen to catch a web
# page parsed wrongly; a model asked how to build a whole environment in
# Blender is not a parsing accident, and cutting it at fifteen and then saying
# "that is as far as the instructions i found go" is untrue - nothing
# truncated it except us.
#
# "Design a full environment" is genuinely thirty-odd steps, and the honest
# way to teach thirty steps is not to say them faster. It is STAGES: the
# terrain, then the lighting, then the materials, each a handful of steps with
# a name, so somebody always knows which part of the job they are in and how
# much of it is left. See `stage_of`.
MAX_DOING_STEPS = 40


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
    # Which part of the job each step belongs to, one name per step, or empty
    # for a lesson short enough not to need parts.
    #
    # A long lesson without this is a flat list, and a flat list of thirty
    # things is the recitation problem again one level up: somebody on step
    # nineteen has no idea whether they are nearly done or barely started, and
    # neither does the model being asked what is next. With it, every step
    # arrives inside something that has a name and an end.
    stage_of: list[str] = field(default_factory=list)
    # What to MARK on screen for each step, one short name per step, or empty.
    #
    # A doing step is an instruction - "open the properties panel and click
    # the blue spanner tab" - and grounding that whole sentence finds
    # nothing. The thing worth circling is a noun inside it, and the model
    # writing the lesson is what knows which noun. So it says, the same way
    # it says which part of the job a step belongs to.
    #
    # Empty is the normal case and means say the step and mark nothing.
    mark_of: list[str] = field(default_factory=list)
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

    # --- which part of the job this is -----------------------------------

    @property
    def staged(self) -> bool:
        """Does this lesson have named parts? Only long ones do."""
        return len(self.stage_of) == len(self.steps) and bool(self.stage_of)

    @property
    def marked(self) -> bool:
        """Does this lesson name something to mark for each step?"""
        return len(self.mark_of) == len(self.steps) and bool(self.mark_of)

    @property
    def to_mark(self) -> str:
        """What to circle for the step they are on now, or ""."""
        if not self.marked or not 0 <= self.index < len(self.mark_of):
            return ""
        return self.mark_of[self.index]

    def stage_at(self, index: int) -> str:
        if not self.staged or not 0 <= index < len(self.stage_of):
            return ""
        return self.stage_of[index]

    @property
    def stage(self) -> str:
        return self.stage_at(self.index)

    @property
    def stages(self) -> list[str]:
        """The parts, in order, each named once."""
        seen: list[str] = []
        for name in self.stage_of if self.staged else []:
            if name and name not in seen:
                seen.append(name)
        return seen

    @property
    def stage_number(self) -> int:
        """Which part they are in, counting from one. Zero if unstaged."""
        if not self.stage:
            return 0
        return self.stages.index(self.stage) + 1

    @property
    def starting_a_stage(self) -> bool:
        """Is the current step the first of a new part?

        The thing worth SAYING. "That is the terrain done - now the lighting"
        is the sentence that makes a thirty step job feel like five jobs, and
        it is only true at the boundary.
        """
        return (bool(self.stage) and self.index > 0
                and self.stage_at(self.index - 1) != self.stage)

    def steps_left_in_stage(self) -> int:
        remaining = 0
        for position in range(self.index, len(self.steps)):
            if self.stage_at(position) != self.stage:
                break
            remaining += 1
        return remaining

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
        if self.staged:
            # Where they are, not only what is next. A long lesson is where
            # somebody most often loses the thread and asks - that is the
            # whole reason parts exist, so the answer says which part.
            return (f"we are on {self.stage.lower()}, "
                    f"{self.steps_left_in_stage()} to go. "
                    f"{self.current.lower()} is next.")
        return f"{self.current.lower()} is next."

    def where_we_are(self) -> str:
        """The lesson, for the prompt. What is done, what is now, what is left.

        The harness had no idea a lesson was running, so every question in
        the middle of one was answered from scratch - and the user said, out
        loud, "you were teaching me how to animate it, did you forget it?"
        They had not forgotten; nothing had ever told them.
        """
        if not self.steps:
            return ""
        lines = [f"YOU ARE TEACHING THEM: {self.goal or self.steps[0]}"]
        if self.staged:
            # The shape of the whole job, before the detail of one step. A
            # long lesson read as a flat list leaves the model unable to
            # answer "how much more of this is there", which is the question
            # somebody halfway through a big job actually asks.
            lines.append(
                f"This is a long one, in {len(self.stages)} parts: "
                + " -> ".join(self.stages))
            lines.append(
                f"They are in part {self.stage_number} of "
                f"{len(self.stages)}, {self.stage}, with "
                f"{self.steps_left_in_stage()} step(s) left in it.")
        if self.walked:
            lines.append("Already done, by them, do not repeat these:")
            lines.extend(f"  - {step}" for step in self.walked)
        lines.append(f"The step they are on NOW: {self.current}")
        rest = self.steps[self.index + 1:]
        if rest:
            lines.append("Still to come, one at a time, do NOT say these yet:")
            lines.extend(f"  - {step}" for step in rest)
        lines.append(
            "Answer whatever they just asked, about THIS. Do not start the "
            "lesson again, do not re-plan it, and do not ask them what they "
            "want to do next - you know what is next and they are waiting "
            "for it. When they say they have done this step, the walkthrough "
            "says the next one itself.")
        return chr(10).join(lines)

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

    def _opening(self) -> str:
        """What a long lesson says before its very first step, or "".

        The count and the first part's name, and nothing else. Somebody being
        taught to build a whole environment needs to know it is five parts
        rather than one endless one - but reading all five names out is the
        recitation this file exists to replace, one level up. They hear each
        part's name as they reach it.
        """
        if not self.staged or self.index != 0:
            return ""
        return (f"this is {len(self.stages)} parts. first, "
                f"{self.stage.lower()}. ")

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
                # A full stop between the step and the prompt, or the two run
                # together as one sentence - "add a plane and scale it up
                # tell me when you have" - and the speech has no pause where
                # the instruction ends.
                return (f"{self._opening()}{self.current.lower()}. "
                        f"tell me when you have.")
            return f"start with {self.current.lower()}. i am pointing at it."
        if progress is Progress.ADVANCED:
            # Said at a boundary and only there. "That is the terrain done -
            # now the lighting" is what turns a thirty step list into five
            # jobs somebody can see the end of; said at every step it would
            # be the padding this file exists to avoid.
            if self.starting_a_stage:
                done = self.stage_at(self.index - 1).lower()
                ending = "" if self.doing else "."
                return (f"that is {done} done. now {self.stage.lower()} - "
                        f"{self.current.lower()}{ending}")
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


def from_steps(steps, goal: str = "", doing: bool = False, stages=None,
               marks=None):
    """A walkthrough from plain steps, or None if it is not worth one.

    One step is not a walkthrough - it is a sentence, and the ordinary reply
    already handles it better. Two or more is a sequence somebody can lose
    their place in, which is the thing this exists for.

    `stages` names the part of the job each step belongs to, one per step. It
    is dropped rather than trusted when the two lists do not line up: a
    mismatch means whoever supplied them lost count, and a lesson whose parts
    are off by one announces "that is the lighting done" in the middle of the
    terrain. A long flat lesson is worse than a staged one and far better
    than a mislabelled one.
    """
    # Materialised first: `steps` may be a generator, and consuming it once
    # to check the lengths would leave nothing to pair with.
    given = list(steps)
    named = list(stages) if stages is not None else []
    if len(named) != len(given):
        named = [""] * len(given)
    # Same guard as the stages: a list that does not line up is DROPPED
    # rather than trusted. A lesson whose marks are off by one circles the
    # wrong thing while saying the right one, which is worse than circling
    # nothing - the whole value of a mark is that it agrees with the words.
    wanted = list(marks) if marks is not None else []
    if len(wanted) != len(given):
        wanted = [""] * len(given)

    kept: list[str] = []
    names: list[str] = []
    targets: list[str] = []
    for step, name, target in zip(given, named, wanted):
        if not str(step).strip():
            continue
        kept.append(str(step).strip())
        names.append(str(name or "").strip())
        targets.append(str(target or "").strip())
    if len(kept) < 2:
        return None

    budget = MAX_DOING_STEPS if doing else MAX_STEPS
    return Walkthrough(steps=kept[:budget], goal=goal, doing=doing,
                       stage_of=names[:budget] if any(names) else [],
                       mark_of=targets[:budget] if any(targets) else [],
                       truncated=len(kept) > budget)
