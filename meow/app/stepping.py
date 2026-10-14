"""A lesson that decides each step when the last one is done.

`guiding.Guide` watches a route whose steps are known in advance. This is
the other kind: nothing is known in advance except the goal, and each step
is decided from the screen as it is at that moment - see
`meow.agent.lesson` for why.

The loop is:

    say the step, circle the thing
        -> the user clicks it            (the render thread notices)
        -> record it, look at the screen, ask for the next step   (a worker)
        -> say it, circle it
        ...

**TWO THREADS, AND THE SPLIT IS NOT NEGOTIABLE.** Noticing the click is two
Win32 calls, so it happens per frame on the render thread, like `Pencil`.
Deciding the next step is a model call and a grounding call - seconds - so it
happens on a worker, because the cat has to keep drawing while it runs.
`tick` therefore never does anything slow, and the worker never touches the
overlay.

**ONE ROUND AT A TIME.** `_working` is what stops a second click, a spoken
"done" and a timeout from each starting their own round - three steps said
at once over each other, which is what a lesson exists to prevent.
"""

from __future__ import annotations

import threading

from ..agent.lesson import Step, StepByStep, Teacher
from .clicking import Clicked, Watch

# The marks for the step somebody is on. Their own group, cleared as each
# step replaces the last, so step four does not leave step three's ring
# beside it - and pinned, because a step takes a minute to follow and a ring
# that faded after six seconds leaves somebody looking at where it used to
# be.
GROUP = "lesson-step"

# What the ring says when the thing was found by SIGHT rather than in the
# tree. Not the ring's size - `clicking.SIGHTED_RADIUS` owns that - this is
# just how big it is drawn, and it matches so the picture does not promise
# more precision than the acceptance does.
RING_RADIUS = 46


class Stepping:
    """One live step-by-step lesson, or none.

    Everything that touches Windows or a model is injected, so the whole
    state machine is testable with lists and fakes - which matters more here
    than usual, because the real thing needs a GUI, a mouse and two API
    calls to run once.
    """

    def __init__(self, say, locate, screen, mark=None, clear=None,
                 teacher=None, watch=None) -> None:
        self.say = say
        # Resolves a spoken target name to a `Target`. The app's, because
        # grounding goes tree-first and then by sight and the harness owns
        # both.
        self.locate = locate
        # Returns the message carrying the current screen, for the planner.
        self.screen = screen
        self.mark = mark
        self.clear = clear
        self._teacher = teacher
        self.watch = watch if watch is not None else Watch()
        self.lesson: StepByStep | None = None
        self.step: Step | None = None
        self.last_error = ""
        self._working = False
        self._lock = threading.Lock()

    # --- state ------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.lesson is not None

    @property
    def waiting_for_a_click(self) -> bool:
        return self.watch.armed

    def where_we_are(self) -> str:
        """For the harness prompt, when a question interrupts the lesson."""
        return self.lesson.where_we_are() if self.lesson is not None else ""

    # --- starting and stopping -------------------------------------------

    def begin(self, goal: str) -> bool:
        """Start teaching this goal. The first step follows on a worker."""
        if not goal.strip():
            return False
        if self._teacher is None:
            try:
                self._teacher = Teacher()
            except Exception as error:  # noqa: BLE001 - say so, do not crash
                self.last_error = f"{type(error).__name__}: {error}"
                return False
        self.lesson = StepByStep(goal=goal.strip())
        self.step = None
        self._round_later()
        return True

    def stop(self) -> None:
        """End the lesson and take the ring with it.

        A ring left behind points at a step nobody is on, which is worse
        than no mark at all.
        """
        self.lesson = None
        self.step = None
        self.watch.disarm()
        if self.clear is not None:
            try:
                self.clear(GROUP)
            except Exception:  # noqa: BLE001 - a stuck mark is not a crash
                pass

    # --- the render thread ------------------------------------------------

    def tick(self) -> None:
        """One frame. Does nothing at all unless a click is being waited for."""
        if self.lesson is None or not self.watch.armed:
            return
        what = self.watch.tick()
        if what is Clicked.WAITING:
            return

        self.watch.disarm()
        if what is Clicked.ON_TARGET:
            self._completed("clicked")
            return
        if what is Clicked.OFF_TARGET:
            # NO NAG. They clicked something else, which means they are doing
            # their own thing - and a lesson that says "that was not the
            # Add Modifier button" to somebody who has moved on is the thing
            # people close applications over. It ends, quietly.
            self.stop()
            return
        if what is Clicked.STEPPED_AWAY:
            self.stop()
            return
        # Four minutes with the ring up and no click. Said once, because
        # silence is not an answer and is also not invisible.
        self.say("i will leave that there - say next when you have done it, "
                 "or stop to leave it")

    # --- the worker -------------------------------------------------------

    def _round_later(self) -> None:
        """Decide the next step off the render thread."""
        with self._lock:
            if self._working:
                return
            self._working = True
        threading.Thread(target=self._round, daemon=True,
                         name="lesson-step").start()

    def _round(self) -> None:
        try:
            self._decide()
        except Exception as error:  # noqa: BLE001 - a lesson may end, never crash
            self.last_error = f"{type(error).__name__}: {error}"
            self.say("i have lost track of that one, sorry")
            self.stop()
        finally:
            with self._lock:
                self._working = False

    def _decide(self) -> None:
        lesson = self.lesson
        if lesson is None:
            return
        if lesson.spent:
            # The budget. A lesson this long is lost rather than nearly
            # finished, and saying so beats another twenty rings.
            self.say("that is as far as i can usefully take you in one go - "
                     "ask me again for the next part")
            self.stop()
            return

        step = self._teacher.next_step(lesson, self.screen())

        # A CRAMMED STEP IS SENT BACK ONCE. Measured in the written lesson,
        # which produced "select the sphere, press i, select location and set
        # a keyframe at frame 1 and frame 10" - several actions under one
        # ring, which is the recitation this exists to replace hidden where
        # the pacing cannot reach it. Affordable here because the user is
        # already waiting for the next step after their click, and it only
        # happens when it happens.
        if step is not None and step.crammed:
            lesson.record(step.instruction,
                          "TOO LONG - that was several actions in one step, "
                          "and was not said. Give only the FIRST of them")
            step = self._teacher.next_step(lesson, self.screen())
            lesson.walked.pop()

        # THE SAME STEP TWICE MEANS THE CLICK DID NOTHING. Seen live, three
        # rounds running on an unchanged screen: "click Show command menu",
        # clicked, "click Show command menu", clicked. The history said so
        # plainly - the prompt says not to re-emit a target that has already
        # opened - and it repeated anyway, because from the screen alone the
        # same step genuinely is the right answer.
        #
        # So it is told the concrete thing the history cannot convey: that
        # nothing changed. Once. A second repeat is not a planning mistake,
        # it is a click that does not work, and the user is the only one who
        # can do anything about it.
        if step is not None and self._repeats_the_last(step):
            lesson.record(step.instruction,
                          "ALREADY DONE and the screen did NOT change - that "
                          "click achieved nothing visible, so do not name it "
                          "again. Either the thing is somewhere else, or they "
                          "need a different route to it")
            step = self._teacher.next_step(lesson, self.screen())
            lesson.walked.pop()
            if step is not None and self._repeats_the_last(step):
                self.say("that does not seem to be doing anything - have a "
                         "look and tell me what you see")
                self.stop()
                return

        if step is None or not step.instruction:
            self.last_error = lesson.last_error or "no step"
            self.say("i cannot tell what to do next from what is on screen")
            self.stop()
            return

        if step.lost:
            # Honest, and the one case worth saying out loud: the model could
            # not find the goal on this screen at all, which usually means
            # they are in the wrong window.
            self.say(step.instruction or "i cannot see where that is from here")
            self.stop()
            return

        if step.done:
            self.say(step.instruction)
            self.stop()
            return

        self.step = step
        self.say(step.instruction)

        if not step.wants_a_click:
            # A keystroke, a drag, a slider, a colour. Nothing to watch for,
            # so it waits to be told - and anything named is still circled,
            # because "drag this handle" wants the handle pointed at even
            # though the drag cannot be detected.
            if step.target:
                self._circle(step.target)
            return

        target = self._circle(step.target)
        if target is None:
            # The words were right and we could not find them on screen. The
            # instruction has already been SAID, which is most of the value,
            # so it falls back to being told rather than apologising - the
            # same rule as a mark that cannot be placed during a written
            # lesson.
            return
        self.watch.arm(target)

    def _repeats_the_last(self, step) -> bool:
        """Is this the step that was just completed, word for word?

        Compared on the TARGET as well as the words, because "click the
        wrench tab" and "click the wrench" are the same step phrased twice
        and both name the same thing.
        """
        if self.lesson is None or not self.lesson.walked:
            return False
        before = self.lesson.walked[-1].instruction.strip().lower()
        now = step.instruction.strip().lower()
        if before and before == now:
            return True
        last = self.step
        return bool(last is not None and step.target
                    and last.target.strip().lower() == step.target.strip().lower())

    def _circle(self, what: str):
        """Draw the ring, and return the target so a click can be matched.

        One group, cleared first, so each step replaces the last.
        """
        if self.clear is not None:
            try:
                self.clear(GROUP)
            except Exception:  # noqa: BLE001
                pass
        try:
            target = self.locate(what)
        except Exception as error:  # noqa: BLE001 - a missing ring is not a crash
            self.last_error = f"{type(error).__name__}: {error}"
            return None
        if target is None:
            return None
        if self.mark is not None:
            try:
                self.mark(target)
            except Exception:  # noqa: BLE001
                pass
        return target

    def _completed(self, how: str) -> None:
        if self.lesson is None or self.step is None:
            return
        self.lesson.record(self.step.instruction, how)
        self._round_later()

    # --- what they say ----------------------------------------------------

    def answer(self, transcript: str) -> bool:
        """Deal with a sentence aimed at the lesson. True if it was.

        Gets first refusal, exactly as `Guide.answer` does and for the same
        reason: "next" is not a classification problem, and a sentence that
        needs a round trip to be understood is not a reflex. Every branch is
        a whole-sentence or phrase match with no model in it.
        """
        from ..language.phrases import (asks_to_repeat, cannot_find_it,
                                        moving_on, wants_to_stop_following)

        if self.lesson is None:
            return False

        if wants_to_stop_following(transcript):
            self.say("alright, stopping there")
            self.stop()
            return True

        if asks_to_repeat(transcript):
            if self.step is not None:
                self.say(self.step.instruction)
            return True

        if cannot_find_it(transcript):
            # They cannot see what was circled. Asking the planner again
            # with the same screen would return the same step, so this says
            # what the ring is for and leaves it up rather than looping.
            if self.step is not None and self.step.target:
                self.say(f"it is {self.step.target} - i have put a ring "
                         f"round it, hold ctrl shift m and circle it if you "
                         f"still cannot see it")
            else:
                self.say("hold ctrl shift m and circle where you are stuck")
            return True

        if moving_on(transcript):
            # Believed, not verified - somebody volunteering that they have
            # done it is not asking to be checked up on. For a click step
            # this is them telling us about a click the watcher missed,
            # which is the right way round: the user is the authority and
            # the watcher is the convenience.
            self.watch.disarm()
            self._completed("told me")
            return True

        return False
