"""Watching somebody follow a route, and saying the next step when they do.

The state machine is `meow.agent.walkthrough` and knows nothing about time or
screens. This is the part that gives it eyes: a thread that reads the
foreground window every couple of seconds while a walkthrough is live, and
pushes a sentence into the reply queue when there is something to say.

**On its own thread, not the render loop.** A UIA digest is 268ms typical and
1.18s for VS Code at full depth. Polling that between frames would stutter the
cat at exactly the moment somebody is watching it point at things.

**It stops the moment they say something else.** A walkthrough is help, not a
mode you have to escape. The next sentence out of their mouth cancels it,
whatever that sentence is - asking how to do a different thing, changing their
mind, or telling the cat to be quiet.
"""

from __future__ import annotations

import threading

from ..agent.walkthrough import Progress, Walkthrough, from_directions

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
        walkthrough = from_directions(directions, goal)
        if walkthrough is None:
            return False
        self.cancel()
        self.walkthrough = walkthrough
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True,
                                        name="walkthrough")
        self._thread.start()
        return True

    def cancel(self) -> None:
        """Stop watching. Safe to call when nothing is running."""
        self._stop.set()
        self.walkthrough = None

    def _watch(self) -> None:
        from ..desktop.uia import digest_foreground

        walkthrough = self.walkthrough
        while walkthrough is not None and not self._stop.wait(POLL_SECONDS):
            if self.should_stop is not None and self.should_stop():
                return
            if self.walkthrough is not walkthrough:
                return                      # replaced by a newer one
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
