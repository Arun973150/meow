"""Handing work over - background tasks with their own window.

Some requests take half a minute. Researching something, filling a spreadsheet,
working through six steps. Until now the cat did them in the foreground: it
stopped listening, did the thing, and came back. That is fine for "click save"
and wrong for "research solar panel costs" - the user is left watching a cat
think, unable to ask for anything else.

So those are handed over. A task gets its own thread and its own small window,
the cat says it has started and goes straight back to listening, and the window
reports as it goes.

Four things follow from that, and they are the requirements rather than
embellishments:

**The cat stays free.** Nothing here runs on the render loop or blocks the
voice loop. A task is a thread and a message queue.

**Instructions can arrive mid-flight.** Say something else about a running task
and it queues behind what is already happening, rather than interrupting it or
being lost. Interrupting a half-written spreadsheet to add a column produces
neither.

**It says when it is done, and waits.** A task that finishes and vanishes
leaves the user unsure whether it worked. It stays on screen until dismissed,
which is also when follow-up questions are easiest to ask.

**It can be stopped.** The panic key reaches tasks as well as the foreground,
and a task checks between steps rather than only at the start.

Clicky's commercial build has something like this - "drop the agent magic word
and it spawns a background agent" - but the implementation is not public and the
open version has no agents at all. This is built from the requirement, not
copied.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class TaskState(Enum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    STOPPED = "stopped"

    @property
    def finished(self) -> bool:
        return self is not TaskState.RUNNING


@dataclass(frozen=True)
class Line:
    """One thing a task has to report."""

    text: str
    kind: str = "say"     # say | step | error | queued
    at: float = field(default_factory=time.perf_counter)


# How many lines a task window keeps. Enough to see what happened, few enough
# that the window stays small and the memory does not grow with a long task.
MAX_LINES = 40

_numbers = itertools.count(1)


class Task:
    """One handed-over piece of work.

    The thread writes; the render loop reads. Everything shared is behind a
    lock except the state enum, which is a single attribute assignment and so
    is atomic under the GIL.
    """

    def __init__(self, goal: str) -> None:
        self.number = next(_numbers)
        self.goal = goal
        self.state = TaskState.RUNNING
        self.started_at = time.perf_counter()
        self.finished_at: float | None = None
        self.summary = ""
        self.acknowledged = False  # the user has been told it finished
        # What the task actually produced - findings, a file path, text. Kept
        # so "paste the results here" has something to paste; without it the
        # work exists only as sentences that have already been spoken.
        self.result = ""
        self.skipped: list[str] = []
        # Which conversation in the record this task writes into. Set by the
        # caller once the task starts, and read by the render loop to put an
        # icon on screen for it - so a task carries its own identity rather
        # than the two being matched up by number somewhere else.
        self.conversation = 0

        self._lines: list[Line] = []
        self._lock = threading.Lock()
        self._instructions: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()

    # --- what the task reports ------------------------------------------

    def log(self, text: str, kind: str = "say") -> None:
        if not text.strip():
            return
        with self._lock:
            self._lines.append(Line(text.strip(), kind))
            if len(self._lines) > MAX_LINES:
                del self._lines[:-MAX_LINES]

    def lines(self) -> list[Line]:
        with self._lock:
            return list(self._lines)

    def last_line(self) -> str:
        with self._lock:
            return self._lines[-1].text if self._lines else ""

    @property
    def seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.perf_counter()
        return end - self.started_at

    @property
    def title(self) -> str:
        goal = " ".join(self.goal.split())
        return goal if len(goal) <= 46 else goal[:45] + "…"

    # --- instructions arriving while it runs ----------------------------

    def add_instruction(self, text: str) -> None:
        """Queue something to do after the current work.

        Queued rather than applied immediately on purpose. Interrupting a
        half-written spreadsheet to add a column produces neither the
        spreadsheet nor the column.
        """
        self._instructions.put(text)
        self.log(f"queued: {text}", kind="queued")

    def take_instruction(self) -> str | None:
        try:
            return self._instructions.get_nowait()
        except queue.Empty:
            return None

    @property
    def queued(self) -> int:
        return self._instructions.qsize()

    # --- stopping --------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    @property
    def should_stop(self) -> bool:
        return self._stop.is_set()


# A worker is handed the task and does the work, logging as it goes.
Worker = Callable[[Task], str]


def declining_confirmer(task: "Task") -> Callable[[str], bool]:
    """Permission for a handed-over task: no, and say so in the window.

    A background task must never ask the voice loop for permission. It did,
    once, and the result was the worst of both: the task sat blocked for
    twenty seconds waiting for a yes, and the user - who had moved on, which
    is the entire point of handing work over - got "open excel?" out of
    nowhere followed by "say yes or no."

    Consent has to be given knowing what is being consented to, and someone
    who has moved on to something else is not in a position to know. So
    anything needing a real decision is declined and written into the task
    window, where it can be read when convenient and redone in the foreground
    where the question makes sense.

    Nearly nothing reaches this. The risk policy already lets through whatever
    the user named themselves, which covers most of what a task does.
    """
    def confirm(question: str) -> bool:
        task.log(f"skipped, needs you: {question.rstrip('?')}", kind="queued")
        return False

    return confirm


class TaskRunner:
    """Starts tasks, keeps them, and tidies them away when dismissed."""

    def __init__(self, limit: int = 3) -> None:
        # More than a few at once and the windows cover the screen the cat is
        # supposed to be helping with.
        self.limit = limit
        self.tasks: list[Task] = []
        self._lock = threading.Lock()

    @property
    def running(self) -> list[Task]:
        with self._lock:
            return [task for task in self.tasks if task.state is TaskState.RUNNING]

    @property
    def visible(self) -> list[Task]:
        """Everything with a window - running, or finished and not dismissed."""
        with self._lock:
            return list(self.tasks)

    def newest_running(self) -> Task | None:
        running = self.running
        return running[-1] if running else None

    def can_start(self) -> bool:
        return len(self.running) < self.limit

    def spawn(self, goal: str, worker: Worker) -> Task:
        """Start a task on its own thread and return it immediately."""
        task = Task(goal)
        with self._lock:
            self.tasks.append(task)

        def run() -> None:
            try:
                summary = worker(task)
                if task.should_stop:
                    task.state = TaskState.STOPPED
                    task.summary = "stopped"
                else:
                    task.state = TaskState.DONE
                    task.summary = summary or "done"
            except Exception as error:  # noqa: BLE001 - a task may fail alone
                task.state = TaskState.FAILED
                task.summary = f"{type(error).__name__}: {error}"
                task.log(task.summary, kind="error")
            finally:
                task.finished_at = time.perf_counter()

        threading.Thread(target=run, name=f"task-{task.number}",
                         daemon=True).start()
        return task

    def dismiss(self, task: Task) -> None:
        with self._lock:
            if task in self.tasks:
                self.tasks.remove(task)

    def dismiss_finished(self) -> int:
        """Close every finished window. Returns how many went."""
        with self._lock:
            finished = [t for t in self.tasks if t.state.finished]
            for task in finished:
                self.tasks.remove(task)
        return len(finished)

    def stop_all(self) -> None:
        """The panic key. Tasks check between steps and wind up."""
        for task in self.running:
            task.stop()

    def newly_finished(self) -> list[Task]:
        """Tasks that have just finished and not yet been mentioned."""
        ready = [task for task in self.visible
                 if task.state.finished and not task.acknowledged]
        for task in ready:
            task.acknowledged = True
        return ready
