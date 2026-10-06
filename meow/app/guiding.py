"""Watching somebody follow a route, and saying the next step when they do.

The state machine is `meow.agent.walkthrough` and knows nothing about time or
screens. This is the part that gives it eyes: a thread that reads the
foreground window every couple of seconds while a walkthrough is live, and
pushes a sentence into the reply queue when there is something to say.

**On its own thread, not the render loop.** A UIA digest is 268ms typical and
1.18s for VS Code at full depth. Polling that between frames would stutter the
cat at exactly the moment somebody is watching it point at things.

**A sentence PAUSES it. Only a new request ends it.** It used to be cancelled
by whatever they said next, on the reasoning that a walkthrough is help rather
than a mode you have to escape. That reasoning is right and the implementation
threw away the wrong thing: "ok what next" destroyed the thing that knew what
next was, and "i can't find it" destroyed the thing that could point at it.

So `answer()` handles the four sentences somebody actually says mid-route -
carry on, say that again, i cannot find it, stop - locally, with no model and
no routing call, and the loop only cancels when it is a genuinely new request.
"""

from __future__ import annotations

import threading

from ..agent.walkthrough import (Progress, Walkthrough,
                                 from_directions, from_steps)

# Slower than the eye, faster than impatience. A person clicking through
# Settings takes a second or two per step, and polling faster only spends UIA
# time to learn the same thing.
POLL_SECONDS = 1.6


class Guide:
    """The live walkthrough, if there is one."""

    def __init__(self, say, point=None, should_stop=None) -> None:
        self.say = say
        self.point = point
        self.should_stop = should_stop
        self.walkthrough: Walkthrough | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def active(self) -> bool:
        return self.walkthrough is not None and not self.walkthrough.finished

    def begin(self, directions, goal: str = "") -> bool:
        """Start walking somebody through a route. False if it is not worth it."""
        return self._take(from_directions(directions, goal))

    def teach(self, steps, goal: str = "") -> bool:
        """Walk somebody through a PROCEDURE the model knew, not a route.

        "Add a UV sphere, set a keyframe, move to a later frame, set another"
        is four steps and no tree contains any of them. Said in one breath it
        is the recitation a walkthrough exists to replace - which is exactly
        what happened the first time teaching Blender worked at all.

        These are not watched for: there is no name to see. They advance when
        the person says they have done it.
        """
        return self._take(from_steps(steps, goal, doing=True))

    def _take(self, walkthrough) -> bool:
        if walkthrough is None:
            return False
        self.cancel()
        self.walkthrough = walkthrough
        if not walkthrough.doing:
            # A doing walkthrough has nothing to poll for, so it does not get
            # a thread. Starting one would read a UIA digest every 1.6
            # seconds to learn nothing.
            self._start_watching(walkthrough)
        return True

    def _start_watching(self, walkthrough) -> None:
        stop = threading.Event()
        self._stop = stop
        self._thread = threading.Thread(
            target=self._watch, args=(walkthrough, stop), daemon=True,
            name="walkthrough")
        self._thread.start()

    def cancel(self) -> None:
        """Stop watching and forget the route. For a genuinely new request."""
        self._stop.set()
        self.walkthrough = None

    def pause(self) -> None:
        """Stop watching, keep the route. For anything they say mid-step.

        The thread is stopped rather than left spinning: a digest every 1.6
        seconds while somebody is talking spends UIA time to learn nothing,
        and a walkthrough that narrates over a question is worse than one
        that waits.
        """
        if self.walkthrough is None:
            return
        self._stop.set()
        self.walkthrough.pause()

    def _watch_again(self) -> None:
        """Put the eyes back on a paused route."""
        if self.walkthrough is None or self.walkthrough.finished:
            return
        self._start_watching(self.walkthrough)

    def answer(self, transcript: str) -> bool:
        """Deal with a sentence aimed at the walkthrough. True if it was.

        False means this was not about the route, and the caller should cancel
        and handle it normally. Returning False is the ONLY path that ends a
        walkthrough by accident, which is why every branch here is an exact
        phrase match rather than anything a model weighs.
        """
        walkthrough = self.walkthrough
        if walkthrough is None or walkthrough.finished:
            return False

        from ..language.phrases import (
            asks_to_repeat,
            cannot_find_it,
            following_along,
            wants_the_next_step,
            wants_to_stop_following,
        )

        if wants_to_stop_following(transcript):
            sentence = walkthrough.stopped()
            self.cancel()
            if sentence:
                self.say(sentence)
            return True

        # `following_along` is the loose one and it goes LAST of the three,
        # so "stop" and "say that again" are still heard as themselves. It
        # is only reached because a lesson is live.
        if (wants_the_next_step(transcript)
                or self._reports_doing(transcript)
                or (walkthrough.doing and following_along(transcript))):
            # They say they did it. BELIEVED, not verified - the watcher
            # exists because looking beats asking, and somebody who has
            # volunteered "done" is not asking to be checked up on. If they
            # are wrong the next poll notices and says it cannot see the
            # step.
            if walkthrough.doing:
                # Nothing is watching, so saying so IS the advance.
                progress = walkthrough.advance()
                sentence = walkthrough.say(progress)
            else:
                sentence = walkthrough.resume()
                self._watch_again()
            if sentence:
                self.say(sentence)
            return True

        if asks_to_repeat(transcript):
            walkthrough.resume()
            self._watch_again()
            self.say(walkthrough.said_again())
            self._point_now()
            return True

        if cannot_find_it(transcript):
            walkthrough.resume()
            self._watch_again()
            self.say(walkthrough.help_me_find_it())
            self._point_now()
            return True

        return False

    def _reports_doing(self, transcript: str) -> bool:
        """"I've added the ball now" - a report of having done the step.

        Only ever consulted while a DOING walkthrough is live, which is what
        makes it safe to be loose: outside one, the same sentence is an
        ordinary request and must route normally. Inside one, there is a step
        on the table and this is the only thing it could be about.
        """
        walkthrough = self.walkthrough
        if walkthrough is None or not walkthrough.doing:
            return False
        # `without_split_contractions`, NOT `spoken_words` - the latter
        # turns an apostrophe into a space, so "I've added" arrives as "i ve
        # added" and every phrase below misses. Written down once already,
        # and still the easiest mistake in this file to make twice.
        from ..language.phrases import without_split_contractions

        said = without_split_contractions(transcript)
        return any(phrase in said for phrase in (
            "i have done", "ive done", "i did", "done it", "added it",
            "i added", "ive added", "i have added", "that is done",
            "thats done", "its done", "it is done", "finished it",
            "i pressed", "ive pressed", "i clicked", "ive clicked",
            "i selected", "ive selected", "i made", "ive made"))

    def _point_now(self) -> None:
        """Point at the current step, reading the screen once to do it."""
        if self.point is None or self.walkthrough is None:
            return
        from ..desktop.uia import digest_foreground

        try:
            digest = digest_foreground()
        except Exception:  # noqa: BLE001 - a bad read is not worth a crash
            return
        self.point(self.walkthrough.current, digest)

    def _watch(self, walkthrough: Walkthrough, stop: threading.Event) -> None:
        """Poll the screen for one walkthrough until told to stop.

        Both arguments are passed IN rather than read off self, and that is
        not tidiness. `self._stop` is replaced every time a paused route is
        picked back up, so a thread reading it per iteration can wake up
        waiting on the event belonging to its own replacement - two watchers
        narrating the same route, a few hundred milliseconds apart. Each
        thread owns the event it was started with, and stops when it is no
        longer the current one.
        """
        from ..desktop.uia import digest_foreground

        while not stop.wait(POLL_SECONDS):
            if self.should_stop is not None and self.should_stop():
                return
            if self.walkthrough is not walkthrough or self._stop is not stop:
                return                      # paused, or replaced by a newer one
            try:
                digest = digest_foreground()
            except Exception:  # noqa: BLE001 - a bad read is not a reason to
                continue                    # abandon somebody mid-route
            progress = walkthrough.observe(digest)
            sentence = walkthrough.say(progress)
            if sentence:
                self.say(sentence)
            if progress in (Progress.ARRIVED, Progress.ADVANCED) and self.point:
                # Pointing is Risk.SAFE, and it is most of the value: the
                # difference between "now personalisation" and knowing WHERE
                # personalisation is on a page of thirty things.
                self.point(walkthrough.current, digest)
            if walkthrough.finished:
                return
