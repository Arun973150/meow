"""Work that repeats, without being asked again.

"Give me a daily briefing." "Check my inbox every couple of hours." Those are
the same shape as any other handed-over job except for when they start, and
the whole difference is that nobody is there to say go.

**A routine is a GOAL and a schedule, not a recorded plan.** The plan is made
fresh each time it fires, for the same reason a recipe is knowledge rather
than a script: a plan recorded on Tuesday describes a screen that has moved
by Thursday. What is stored is the sentence the user said.

**It is durable, because a routine that forgets is worse than none.** A daily
briefing that silently stops when the machine reboots is a feature somebody
relies on once. `%LOCALAPPDATA%/Meow/routines.db`, beside the plans - never
in Documents, because a synced folder copies a database and its write-ahead
log independently and on its own schedule.

**It runs while Meow is open, and it is honest about that.** There is no
service and no scheduled task: this is a desktop companion, and a background
process that wakes the machine to read somebody's mail is a different
product with different consent attached to it.

Nothing here starts anything. It answers "what is due" and records "this ran",
and the loop owns the firing - so the whole thing can be tested by moving a
clock.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field

# The shortest repeat worth having. Below this a routine is a loop, and a loop
# that opens applications and reads mail is a thing somebody has to notice is
# running before it has spent their month's credit.
MINIMUM_MINUTES = 15

# How long after Meow starts before anything is allowed to fire. A routine
# that goes off during startup competes with the window the user just opened,
# the chat process coming up and the first UIA warm-up - and "never start
# without a usable connection" is the same thought: a briefing that fires
# into a dead network reports a failure nobody caused.
SETTLE_SECONDS = 90.0

# A routine that missed its slot while the machine was off does NOT fire a
# catch-up for every slot it missed. Eight hours asleep must not produce four
# briefings at breakfast.
CATCH_UP = False


@dataclass
class Routine:
    """One repeating job."""

    goal: str
    minutes: int
    # None until it has run once. Stored as a wall-clock epoch rather than a
    # monotonic one, because the whole point is to survive a restart.
    last_run: float | None = None
    created: float = field(default_factory=time.time)
    identifier: int = 0
    paused: bool = False

    @property
    def description(self) -> str:
        return f"{self.goal} - {describe_every(self.minutes)}"

    def due_at(self) -> float:
        """When this should next run, as an epoch."""
        if self.last_run is None:
            return self.created
        return self.last_run + self.minutes * 60

    def is_due(self, now: float | None = None) -> bool:
        if self.paused:
            return False
        return (now if now is not None else time.time()) >= self.due_at()


# --- what somebody said, as a number of minutes ------------------------------

# Spelled numbers, because nobody says "every 2 hours" out loud and the
# transcriber writes what it hears.
WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "sixty": 60, "half": 0,
    "couple": 2, "few": 3,
}

# Named times of day. A routine is not a calendar and does not try to be: the
# point is "about this often", and anybody wanting 09:14 on Tuesdays wants a
# calendar entry rather than a cat.
NAMED = {
    "every morning": 24 * 60,
    "each morning": 24 * 60,
    "every day": 24 * 60,
    "each day": 24 * 60,
    "daily": 24 * 60,
    "every evening": 24 * 60,
    "every night": 24 * 60,
    "twice a day": 12 * 60,
    "every hour": 60,
    "hourly": 60,
    "every week": 7 * 24 * 60,
    "weekly": 7 * 24 * 60,
    "every half hour": 30,
    "every half an hour": 30,
}

# `minutes?` rather than `minute|minutes`: alternation is first-match, so the
# singular won and left the trailing "s" behind - "check my inbox every two
# hours" became the goal "check my inbox s".
EVERY = re.compile(
    r"every\s+(?:(\d+)|([a-z]+))?\s*(minutes?|hours?|days?|weeks?)",
    re.IGNORECASE)


def how_often(said: str) -> int | None:
    """Minutes between runs, or None when the sentence says no schedule.

    None rather than a guess. A routine that fires on a cadence nobody asked
    for is the kind of thing somebody discovers from their API bill, and
    "every" is a word that turns up in sentences that are not schedules at
    all - "do every one of these".
    """
    words = " ".join(str(said).lower().split())

    for phrase, minutes in NAMED.items():
        if phrase in words:
            return minutes

    matched = EVERY.search(words)
    if not matched:
        return None

    digits, spelled, unit = matched.groups()
    if digits:
        count = int(digits)
    elif spelled:
        if spelled not in WORD_NUMBERS:
            return None
        count = WORD_NUMBERS[spelled]
    else:
        count = 1

    unit = unit.rstrip("s")
    per_unit = {"minute": 1, "hour": 60, "day": 24 * 60, "week": 7 * 24 * 60}
    minutes = count * per_unit[unit]
    return max(MINIMUM_MINUTES, minutes) if minutes > 0 else None


def describe_every(minutes: int) -> str:
    """Said the way a person would. For the ear, so no digits where a word
    will do."""
    if minutes % (7 * 24 * 60) == 0:
        weeks = minutes // (7 * 24 * 60)
        return "every week" if weeks == 1 else f"every {weeks} weeks"
    if minutes % (24 * 60) == 0:
        days = minutes // (24 * 60)
        return "every day" if days == 1 else f"every {days} days"
    if minutes % 60 == 0:
        hours = minutes // 60
        return "every hour" if hours == 1 else f"every {hours} hours"
    return f"every {minutes} minutes"


def without_the_schedule(said: str) -> str:
    """The job, with the timing taken out of it.

    "Every morning give me a briefing" is a routine whose goal is "give me a
    briefing" - leaving the schedule in means the plan it makes each day has
    a step about mornings in it.
    """
    words = " ".join(str(said).split())
    lowered = words.lower()
    for phrase in sorted(NAMED, key=len, reverse=True):
        index = lowered.find(phrase)
        if index >= 0:
            words = words[:index] + words[index + len(phrase):]
            lowered = words.lower()
    words = EVERY.sub("", words)
    # Leading connectives left behind by the cut - "and give me a briefing".
    words = re.sub(r"^\s*(and|then|also|please|,)\s+", "", words.strip(),
                   flags=re.IGNORECASE)
    return " ".join(words.split()).strip(" ,.")


# --- where they live ---------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS routines (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    goal     TEXT    NOT NULL,
    minutes  INTEGER NOT NULL,
    created  REAL    NOT NULL,
    last_run REAL,
    paused   INTEGER NOT NULL DEFAULT 0
)
"""


def database_path():
    from ..storage.paths import app_data

    return app_data() / "routines.db"


class RoutineBook:
    """Every routine, on disk.

    One connection per thread, like the conversation store, and for the same
    reason: SQLite connections are not shareable and the loop, the tasks and
    the chat window are all different threads.
    """

    def __init__(self, path=None) -> None:
        self.path = path or database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._connection() as connection:
            connection.execute(SCHEMA)

    def _connection(self) -> sqlite3.Connection:
        existing = getattr(self._local, "connection", None)
        if existing is None:
            existing = sqlite3.connect(str(self.path), timeout=5.0)
            # WAL so a reader never blocks the writer. Busy timeout rather
            # than a retry loop, which is the same bargain the conversation
            # store makes.
            existing.execute("PRAGMA journal_mode=WAL")
            self._local.connection = existing
        return existing

    def add(self, goal: str, minutes: int) -> Routine:
        routine = Routine(goal=goal, minutes=max(MINIMUM_MINUTES, minutes))
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO routines (goal, minutes, created, last_run, "
                "paused) VALUES (?, ?, ?, NULL, 0)",
                (routine.goal, routine.minutes, routine.created))
            routine.identifier = int(cursor.lastrowid)
        return routine

    def all(self) -> list[Routine]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, goal, minutes, created, last_run, paused "
                "FROM routines ORDER BY id").fetchall()
        return [Routine(identifier=row[0], goal=row[1], minutes=row[2],
                        created=row[3], last_run=row[4], paused=bool(row[5]))
                for row in rows]

    def due(self, now: float | None = None) -> list[Routine]:
        return [routine for routine in self.all() if routine.is_due(now)]

    def mark_ran(self, routine: Routine, when: float | None = None) -> None:
        """Record a run. Called whether it succeeded or not.

        A routine that failed must not retry immediately: a briefing that
        cannot reach the network would otherwise spin as fast as the loop
        checks, which is the one failure mode that costs money while nobody
        is watching.
        """
        stamp = time.time() if when is None else when
        routine.last_run = stamp
        with self._connection() as connection:
            connection.execute("UPDATE routines SET last_run = ? WHERE id = ?",
                               (stamp, routine.identifier))

    def remove(self, identifier: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM routines WHERE id = ?",
                                        (identifier,))
        return cursor.rowcount > 0

    def remove_matching(self, words: str) -> list[Routine]:
        """Delete by what somebody called it. Returns what went.

        Matched on words rather than an id, because nobody remembers the
        number of their own daily briefing.
        """
        wanted = {word for word in re.findall(r"[a-z0-9]+", words.lower())
                  if len(word) > 2}
        if not wanted:
            return []
        gone = []
        for routine in self.all():
            has = set(re.findall(r"[a-z0-9]+", routine.goal.lower()))
            if wanted & has:
                if self.remove(routine.identifier):
                    gone.append(routine)
        return gone

    def close(self) -> None:
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            existing.close()
            self._local.connection = None
