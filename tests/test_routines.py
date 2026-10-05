"""Work that repeats.

The schedule language is pure strings; the store needs a temporary file and
no more. Nothing here starts anything - the loop owns the firing, which is
what lets the whole thing be tested by moving a clock.
"""

from __future__ import annotations

import time

import pytest

from meow.work.routines import (
    MINIMUM_MINUTES,
    Routine,
    RoutineBook,
    describe_every,
    how_often,
    without_the_schedule,
)


@pytest.mark.parametrize("said, minutes", [
    ("give me a daily briefing", 24 * 60),
    ("every morning give me a briefing", 24 * 60),
    ("every day tell me my calendar", 24 * 60),
    ("check my inbox every two hours", 120),
    ("check my inbox every 2 hours", 120),
    ("every 30 minutes check hacker news", 30),
    ("check this every few hours", 180),
    ("twice a day summarise my calendar", 12 * 60),
    ("every week tidy my documents", 7 * 24 * 60),
    ("every half an hour check my mail", 30),
    ("hourly check the weather", 60),
])
def test_how_often_somebody_said(said, minutes):
    assert how_often(said) == minutes


@pytest.mark.parametrize("said", [
    "open notepad",
    "do every one of these steps",
    "check my inbox",
    "what is on my calendar",
    "",
])
def test_no_schedule_is_None_rather_than_a_guess(said):
    """A routine firing on a cadence nobody asked for is the kind of thing
    somebody discovers from a bill. "Every" turns up in sentences that are
    not schedules at all.
    """
    assert how_often(said) is None


def test_nothing_repeats_faster_than_the_floor():
    """Below a quarter of an hour a routine is a loop, and a loop that opens
    applications and reads mail is a thing somebody has to notice is running
    before it has spent their month's credit.
    """
    assert how_often("every minute check my mail") == MINIMUM_MINUTES
    assert how_often("every 2 minutes check my mail") == MINIMUM_MINUTES


@pytest.mark.parametrize("said, goal", [
    ("every morning give me a briefing", "give me a briefing"),
    ("check my inbox every two hours", "check my inbox"),
    ("every 30 minutes check hacker news", "check hacker news"),
    ("give me a daily briefing", "give me a briefing"),
    ("every week tidy my documents", "tidy my documents"),
])
def test_the_timing_comes_out_of_the_goal(said, goal):
    """"Every morning give me a briefing" is a routine whose goal is "give me
    a briefing" - leaving the schedule in means every plan it makes has a
    step about mornings in it.
    """
    assert without_the_schedule(said) == goal


@pytest.mark.parametrize("minutes, said", [
    (24 * 60, "every day"), (60, "every hour"), (120, "every 2 hours"),
    (30, "every 30 minutes"), (7 * 24 * 60, "every week"),
])
def test_it_says_the_schedule_back_the_way_a_person_would(minutes, said):
    assert describe_every(minutes) == said


# --- the store ---------------------------------------------------------------


@pytest.fixture
def book(tmp_path):
    return RoutineBook(tmp_path / "routines.db")


def test_a_new_routine_is_due_at_once(book):
    """Somebody who just asked for a daily briefing should get one today,
    not tomorrow.
    """
    routine = book.add("give me a briefing", 24 * 60)
    assert routine.is_due()
    assert [r.goal for r in book.due()] == ["give me a briefing"]


def test_running_it_pushes_the_next_one_out(book):
    routine = book.add("give me a briefing", 60)
    book.mark_ran(routine)
    assert book.due() == []
    # An hour later, with the clock moved rather than waited out.
    assert len(book.due(time.time() + 61 * 60)) == 1


def test_a_run_is_recorded_whether_or_not_it_worked(book):
    """A routine that failed must not retry as fast as the loop checks -
    the one failure mode that spends money while nobody is watching.
    """
    routine = book.add("check my inbox", 60)
    book.mark_ran(routine)          # as the loop does, BEFORE running it
    assert book.due() == []


def test_it_survives_being_reopened(tmp_path):
    """A daily briefing that silently stops at a reboot is a feature
    somebody relies on once.
    """
    path = tmp_path / "routines.db"
    first = RoutineBook(path)
    routine = first.add("give me a briefing", 24 * 60)
    first.mark_ran(routine)
    first.close()

    again = RoutineBook(path)
    kept = again.all()
    assert [r.goal for r in kept] == ["give me a briefing"]
    assert kept[0].last_run is not None
    assert not kept[0].is_due()


def test_stopping_one_is_matched_on_what_it_is_about(book):
    """Nobody remembers the number of their own daily briefing."""
    book.add("give me a briefing of my inbox", 24 * 60)
    book.add("check hacker news", 180)

    gone = book.remove_matching("stop the briefing")
    assert [r.goal for r in gone] == ["give me a briefing of my inbox"]
    assert [r.goal for r in book.all()] == ["check hacker news"]


def test_stopping_something_that_is_not_there_removes_nothing(book):
    book.add("check hacker news", 180)
    assert book.remove_matching("the quarterly accounts") == []
    assert len(book.all()) == 1


def test_a_paused_routine_never_comes_due():
    routine = Routine(goal="x", minutes=1, paused=True)
    assert not routine.is_due()
