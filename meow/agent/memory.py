"""One memory, shared by everything.

Until now each path remembered separately, and two of them remembered nothing.
`mind` kept four turns for questions it answered. The harness kept none at all -
it started a fresh LangGraph thread per turn, so "click save" followed by "now
the other one" had nothing to resolve "the other one" against. Background tasks
existed entirely outside both, so asking what one was doing reached something
that had never heard of it.

That is why it felt random. Not the model - the plumbing.

So: one `Memory`, read by every path before it answers and written by every
path after. It holds two things.

**What was said.** A short rolling transcript, both sides. Short on purpose:
every turn is recharged in full on the next call, so history is a recurring
cost, and the useful part of a conversation with a desktop assistant is the
last minute of it.

**Who is working.** An actor per running task, with a one-line state. This is
what lets Meow answer "what is the research one doing" without asking the task,
which may be mid-request and cannot be interrupted to reply.

**Actors retire.** When a task is dismissed its actor leaves, and the memory
stops carrying it. A finished task is history, not context, and one that stayed
would take up room in every prompt for the rest of the session - and, worse,
invite the model to keep referring to something that no longer exists.

**What was DONE, not only what was said.** A follow-up is usually three words
and refers to an action rather than to a sentence: "do it again", "now the
other one", "undo that", "the second one". None of those can be resolved from
a transcript, because what the cat SAID about an action is prose written for
the ear - "all done, that is typed in for you" names no tool and no target,
and it is the only trace the action left.

So every tool run is recorded here too: what it was, what it was aimed at, and
whether it worked. It is the cheapest context in the file - a handful of short
lines - and it is the difference between "the other one" resolving and the
model guessing which of the last ten sentences was about a button.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

# A minute or two of talking. Enough for "that one" and "the other one" to
# resolve, short enough that it is not a meaningful share of the prompt.
MAX_TURNS = 10

# Anything longer is a monologue, and quoting it back costs more than it
# clarifies.
MAX_TURN_CHARACTERS = 220

# How many actions are remembered. Short for the same reason the transcript is
# short, and shorter: "the other one" refers to something from the last turn or
# two, never from eight ago, and a long list invites the model to revisit
# something the user has moved on from.
MAX_DEEDS = 6

# Tools whose runs are NOT worth remembering, because they changed nothing a
# follow-up could refer back to. A turn that looked at the screen and then
# answered is already fully described by what was said; recording "looked at
# the screen" six times pushes out the one line that said which button was
# pressed.
NOT_WORTH_REMEMBERING = frozenset({
    "look_at_screen", "find_how_to", "look_up", "read_mail", "read_message",
    "my_agenda", "my_tasks", "check_weather", "hacker_news",
    "read_google_doc", "read_google_sheet", "list_routines",
    "clear_the_screen",
})


@dataclass(frozen=True)
class Said:
    who: str          # "user" or "meow"
    text: str
    at: float = field(default_factory=time.perf_counter)


@dataclass(frozen=True)
class Did:
    """One thing actually done on the machine, and whether it took."""

    tool: str
    target: str
    # True, False, or None for could-not-tell - the verifier's three-way
    # verdict, carried rather than flattened. A follow-up to something that
    # did not work is usually "try again", and a follow-up to something
    # unverifiable is usually "did that work?" - the model cannot answer
    # either from a boolean.
    worked: bool | None = None
    at: float = field(default_factory=time.perf_counter)

    def describe(self) -> str:
        said = f"{self.tool.replace('_', ' ')}"
        if self.target:
            said += f": {self.target}"
        if self.worked is False:
            said += "  (did NOT take effect)"
        elif self.worked is None:
            said += "  (could not tell whether it worked)"
        return said


@dataclass
class Actor:
    """Something working in the background, and what it is up to."""

    name: str
    role: str
    state: str = "starting"
    started_at: float = field(default_factory=time.perf_counter)

    @property
    def seconds(self) -> float:
        return time.perf_counter() - self.started_at

    def describe(self) -> str:
        return f"{self.name} ({self.role}) - {self.state}, {self.seconds:.0f}s in"


class Memory:
    """Shared, bounded, and written from several threads.

    Everything is behind one lock. The contents are small and the operations
    are short, so a single lock costs nothing and removes a whole category of
    question about what happens when a task and the voice loop write at once.
    """

    def __init__(self, max_turns: int = MAX_TURNS) -> None:
        self._turns: deque[Said] = deque(maxlen=max_turns)
        self._deeds: deque[Did] = deque(maxlen=MAX_DEEDS)
        self._actors: dict[str, Actor] = {}
        self._lock = threading.Lock()

    # --- what was said ---------------------------------------------------

    def said(self, who: str, text: str) -> None:
        cleaned = " ".join(str(text).split())
        if not cleaned:
            return
        if len(cleaned) > MAX_TURN_CHARACTERS:
            cleaned = cleaned[:MAX_TURN_CHARACTERS].rstrip() + "…"
        with self._lock:
            self._turns.append(Said(who, cleaned))

    def turns(self) -> list[Said]:
        with self._lock:
            return list(self._turns)

    # --- what was done ---------------------------------------------------

    def did(self, tool: str, target: str, worked: bool | None = None) -> None:
        """Record an action. Reading tools are dropped - see the list."""
        name = str(tool).strip()
        if not name or name in NOT_WORTH_REMEMBERING:
            return
        aimed = " ".join(str(target).split())[:60]
        with self._lock:
            # Replaced rather than appended when it is the same action on the
            # same thing. A plan that presses ctrl+s four times should leave
            # one line, or the six slots fill with one repeated keystroke and
            # push out what the keystroke was done TO.
            if self._deeds and (self._deeds[-1].tool == name
                                and self._deeds[-1].target == aimed):
                self._deeds[-1] = Did(name, aimed, worked)
                return
            self._deeds.append(Did(name, aimed, worked))

    def deeds(self) -> list[Did]:
        with self._lock:
            return list(self._deeds)

    def last_deed(self) -> Did | None:
        with self._lock:
            return self._deeds[-1] if self._deeds else None

    # --- who is working --------------------------------------------------

    def join(self, name: str, role: str) -> Actor:
        """Register something that has started working."""
        actor = Actor(name=name, role=role)
        with self._lock:
            self._actors[name] = actor
        return actor

    def update(self, name: str, state: str) -> None:
        with self._lock:
            actor = self._actors.get(name)
            if actor is not None:
                actor.state = " ".join(str(state).split())[:120]

    def leave(self, name: str) -> None:
        """Retire an actor. Its state stops appearing in prompts.

        Called when a task is dismissed rather than when it finishes: a
        finished task the user has not looked at is still worth being able to
        ask about, and one they have dismissed is not.
        """
        with self._lock:
            self._actors.pop(name, None)

    def actors(self) -> list[Actor]:
        with self._lock:
            return list(self._actors.values())

    # --- what every path reads -------------------------------------------

    def recall(self, include_turns: bool = True,
               without: str | None = None) -> str:
        """The block that goes into a prompt. Empty when there is nothing.

        Returned as one string rather than as messages, so every path can drop
        it in wherever it puts context without agreeing on a message format.

        `without` drops one actor by name. A task reads this memory too, and
        listing the task itself under "working in the background" invites it
        to report on itself in the third person instead of doing the work.
        """
        parts: list[str] = []

        actors = [a for a in self.actors() if a.name != without]
        if actors:
            parts.append("Working in the background right now:\n" + "\n".join(
                f"- {actor.describe()}" for actor in actors))

        deeds = self.deeds()
        if deeds:
            # Before what was said, because it is what a short follow-up
            # refers to. "Now the other one" is about the last action, and
            # the sentence that reported that action does not name it.
            parts.append(
                "What you have actually DONE on their machine, oldest first. "
                "A short follow-up - \"again\", \"the other one\", \"undo "
                "that\" - almost certainly means the last of these:\n"
                + "\n".join(f"- {deed.describe()}" for deed in deeds))

        if include_turns:
            turns = self.turns()
            if turns:
                spoken = "\n".join(
                    f"{'you' if turn.who == 'meow' else 'user'}: {turn.text}"
                    for turn in turns)
                parts.append("Recently said:\n" + spoken)

        return "\n\n".join(parts)

    def recent(self, turns: int = 4) -> str:
        """The last few exchanges, compact, for the router.

        Shorter than `recall`: the router asks a classifier one small question
        and needs only enough to resolve a follow-up. "Can you type about Elon
        Musk" is a question about Elon Musk on its own and an instruction once
        you know Notepad was opened ten seconds ago.
        """
        lines = [f"{'Meow' if turn.who == 'meow' else 'User'}: {turn.text}"
                 for turn in self.turns()[-turns:]]
        # One line about the last ACTION, which is what the router most often
        # cannot see. "Do it again" and "now the other one" are shaped like
        # questions and are instructions, and the only thing that settles it
        # is whether anything was done - the reply that reported it says
        # "all done, that is typed in for you" and names no tool.
        last = self.last_deed()
        if last is not None:
            lines.append(f"(Meow just did: {last.describe()})")
        return chr(10).join(lines)

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()
            self._actors.clear()
