"""Work that repeats, set up by voice.

The store and the schedule language are in `meow/work/routines.py`. These are
the three things somebody says about one: set it up, list them, stop one.

**Making a routine is not a risky action and stopping one is not either.**
Both are reversible in a sentence, and a routine runs the user's own goal on
their own machine - there is no destination argument, so nothing a hostile
page said can choose where it goes. What the routine then DOES still goes
through the same risk gate as anything else, every time it fires.

⚠ **A routine must never be created from something the cat merely inferred.**
"Check my inbox" is one job; "check my inbox every two hours" is a standing
instruction, and the difference is a word the user said. The schedule is
parsed from their sentence rather than chosen by the model, and `how_often`
returns None rather than a guess - a routine firing on a cadence nobody asked
for is the kind of thing somebody discovers from a bill.
"""

from __future__ import annotations

from langchain_core.tools import tool

from ..desktop.actions import Outcome
from ..work.routines import describe_every, how_often, without_the_schedule
from .record import ToolRun


def build(harness) -> list:
    """The tools in this module, bound to one harness."""

    @tool
    def repeat_this(what_to_do: str, how_often_they_said: str) -> str:
        """Set up work that repeats on its own - a routine.

        For "give me a daily briefing", "check my inbox every couple of
        hours", "every morning tell me what is on my calendar".

        what_to_do: the job itself, with no timing in it.
        how_often_they_said: THEIR words about when - "every morning",
        "every two hours", "daily". Pass what they actually said. Never
        invent a schedule: if they did not say how often, ask.

        Routines run while Meow is open. They are not a background service
        and do not wake the machine.
        """
        book = harness.routines
        if book is None:
            return ("Routines are not available in this session, so say so "
                    "rather than claiming it is set up.")

        minutes = how_often(how_often_they_said) or how_often(what_to_do)
        if minutes is None:
            harness.runs.append(ToolRun("repeat_this", what_to_do,
                                        Outcome(False, "no schedule given")))
            return ("They did not say how often. Ask - every morning, every "
                    "few hours, once a week - and do not pick one for them.")

        goal = without_the_schedule(what_to_do) or what_to_do
        routine = book.add(goal, minutes)
        harness.runs.append(ToolRun(
            "repeat_this", f"{goal} / {describe_every(minutes)}",
            Outcome(True, "routine set up")))
        return (f"Set up: {goal!r}, {describe_every(minutes)}. Tell them it "
                f"is set up and how often, and that it runs while you are "
                f"open. The first one runs shortly.")

    @tool
    def list_routines() -> str:
        """What repeats on its own at the moment."""
        book = harness.routines
        if book is None:
            return "Routines are not available in this session."
        existing = book.all()
        harness.runs.append(ToolRun("list_routines", "",
                                    Outcome(True, f"{len(existing)} routines")))
        if not existing:
            return ("Nothing repeats at the moment. Say so, and that they can "
                    "ask for one.")
        lines = "\n".join(f"- {routine.description}" for routine in existing)
        return (f"Set up right now:\n{lines}\n\nRead these out plainly. No "
                f"numbers, no identifiers - they refer to these by what they "
                f"are about.")

    @tool
    def stop_repeating(which_one: str) -> str:
        """Stop a routine. Say which, in the user's own words.

        "Stop the daily briefing", "stop checking my inbox".
        """
        book = harness.routines
        if book is None:
            return "Routines are not available in this session."
        gone = book.remove_matching(which_one)
        harness.runs.append(ToolRun("stop_repeating", which_one,
                                    Outcome(bool(gone), f"{len(gone)} stopped")))
        if not gone:
            existing = book.all()
            if not existing:
                return "Nothing repeats at the moment, so there is none to stop."
            names = "; ".join(routine.goal for routine in existing)
            return (f"Nothing matched {which_one!r}. What is set up: {names}. "
                    f"Ask which one rather than guessing.")
        names = "; ".join(routine.goal for routine in gone)
        return f"Stopped: {names}. Say which one stopped."

    return [repeat_this, list_routines, stop_repeating]
