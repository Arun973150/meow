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


@dataclass(frozen=True)
class Said:
    who: str          # "user" or "meow"
    text: str
    at: float = field(default_factory=time.perf_counter)


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
        return chr(10).join(lines)

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()
            self._actors.clear()
