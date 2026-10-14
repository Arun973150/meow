"""A lesson that decides each step from the screen in front of them.

Every one of these is a case the written-lesson version cannot handle: a
menu opening where a panel was expected, a dialog nobody predicted, the user
being two steps ahead. The point of deciding one step at a time is that none
of them is an error to recover from - the next step comes from wherever they
actually are - so what is tested here is the PACING, not the planning.

The real thing needs a GUI, a mouse and two API calls per step, which is
exactly why none of that is in here.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from meow.agent.lesson import MAX_ROUNDS, Step, StepByStep
from meow.app.clicking import (EXACT_PADDING, SIGHTED_RADIUS, Clicked, Watch,
                               Where)
from meow.app.stepping import Stepping


# --- fakes -------------------------------------------------------------------

@dataclass
class FakeTarget:
    left: int
    top: int
    right: int
    bottom: int
    is_exact: bool = True

    @property
    def centre(self):
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


class FakeTeacher:
    """Hands out scripted steps and records what it was told."""

    def __init__(self, steps) -> None:
        self.steps = list(steps)
        self.histories = []

    def next_step(self, lesson, screen):
        lesson.rounds += 1
        self.histories.append(lesson.so_far())
        return self.steps.pop(0) if self.steps else None


class FakeWatch:
    """A Watch whose verdict is set by the test."""

    def __init__(self) -> None:
        self.armed = False
        self.verdict = Clicked.WAITING
        self.armed_on = None

    def arm(self, target) -> None:
        self.armed = True
        self.armed_on = target

    def disarm(self) -> None:
        self.armed = False

    def tick(self) -> Clicked:
        return self.verdict


def build(steps, found=None):
    """A Stepping with nothing real in it. Returns it and what it said."""
    said: list[str] = []
    marked: list = []
    cleared: list[str] = []
    watch = FakeWatch()
    target = FakeTarget(100, 100, 140, 120) if found is None else found

    lesson = Stepping(
        say=said.append,
        locate=lambda what: target,
        screen=lambda: "a screenshot",
        mark=marked.append,
        clear=cleared.append,
        teacher=FakeTeacher(steps),
        watch=watch,
    )
    # The worker is the thing being tested, so it runs inline rather than on
    # a thread - a test that starts threads measures the scheduler.
    lesson._round_later = lesson._round
    return lesson, said, marked, cleared, watch


# --- the loop ----------------------------------------------------------------

def test_the_first_step_is_said_and_the_thing_is_circled():
    lesson, said, marked, _cleared, watch = build([
        Step("click the wrench tab", target="the wrench tab"),
    ])

    assert lesson.begin("add a modifier")

    assert said == ["click the wrench tab"]
    assert len(marked) == 1, "the thing it named was circled"
    assert watch.armed, "and a click on it is being waited for"


def test_a_click_on_the_thing_brings_the_next_step():
    lesson, said, _marked, _cleared, watch = build([
        Step("click the wrench tab", target="the wrench tab"),
        Step("click add modifier", target="add modifier"),
    ])
    lesson.begin("add a modifier")

    watch.verdict = Clicked.ON_TARGET
    lesson.tick()

    assert said == ["click the wrench tab", "click add modifier"]
    assert watch.armed, "and it waits for the next click"


def test_what_happened_is_handed_to_the_next_round():
    """The whole design: the next step is decided from the screen AND from
    what has already been done. A round that cannot see the history would
    repeat the step it just completed.
    """
    lesson, _said, _marked, _cleared, watch = build([
        Step("click the wrench tab", target="the wrench tab"),
        Step("click add modifier", target="add modifier"),
    ])
    lesson.begin("add a modifier")
    watch.verdict = Clicked.ON_TARGET
    lesson.tick()

    first, second = lesson._teacher.histories
    assert "Nothing yet" in first
    assert "click the wrench tab" in second
    assert "(clicked)" in second, "HOW it completed, not only that it did"


def test_nothing_happens_on_a_frame_with_no_click():
    lesson, said, _marked, _cleared, watch = build([
        Step("click the wrench tab", target="the wrench tab"),
    ])
    lesson.begin("add a modifier")
    watch.verdict = Clicked.WAITING

    for _frame in range(60):
        lesson.tick()

    assert said == ["click the wrench tab"], "one step, said once"
    assert watch.armed


def test_a_keyboard_step_waits_to_be_told_rather_than_watching():
    """There is nothing to click, so arming a click watch would wait for
    something that can never arrive."""
    lesson, said, marked, _cleared, watch = build([
        Step("press tab to go into edit mode", target="", kind="do"),
    ])
    lesson.begin("edit the mesh")

    assert said == ["press tab to go into edit mode"]
    assert not watch.armed
    assert marked == [], "nothing named, so nothing circled"


def test_a_keyboard_step_that_names_something_still_circles_it():
    """"drag this handle" cannot be detected and the handle still wants
    pointing at."""
    lesson, _said, marked, _cleared, watch = build([
        Step("drag the left handle to the right", target="the left handle",
             kind="do"),
    ])
    lesson.begin("trim the clip")

    assert len(marked) == 1
    assert not watch.armed, "a drag is not a click"


def test_saying_done_advances_a_step_nothing_was_watching():
    lesson, said, _marked, _cleared, _watch = build([
        Step("press tab to go into edit mode", target="", kind="do"),
        Step("press a to select everything", target="", kind="do"),
    ])
    lesson.begin("edit the mesh")

    assert lesson.answer("done") is True
    assert said[-1] == "press a to select everything"


def test_being_told_about_a_click_the_watcher_missed():
    """The user is the authority and the watcher is the convenience."""
    lesson, said, _marked, _cleared, watch = build([
        Step("click the wrench tab", target="the wrench tab"),
        Step("click add modifier", target="add modifier"),
    ])
    lesson.begin("add a modifier")

    assert lesson.answer("yeah i clicked it") is True
    assert not watch.armed or watch.armed_on is not None
    assert said[-1] == "click add modifier"


# --- the endings -------------------------------------------------------------

def test_an_off_target_click_ends_the_lesson_with_no_nag():
    """They clicked something else, so they are doing their own thing. A
    lesson that corrects them is the thing people close applications over.
    """
    lesson, said, _marked, cleared, _watch = build([
        Step("click the wrench tab", target="the wrench tab"),
        Step("click add modifier", target="add modifier"),
    ])
    lesson.begin("add a modifier")
    before = len(said)

    lesson._watch_verdict = Clicked.OFF_TARGET
    lesson.watch.verdict = Clicked.OFF_TARGET
    lesson.tick()

    assert not lesson.active
    assert said[before:] == [], "not one word about it"
    assert "lesson-step" in cleared, "and the ring goes"


def test_stepping_away_ends_it():
    lesson, _said, _marked, _cleared, watch = build([
        Step("click the wrench tab", target="the wrench tab"),
    ])
    lesson.begin("add a modifier")

    watch.verdict = Clicked.STEPPED_AWAY
    lesson.tick()

    assert not lesson.active


def test_the_lesson_ends_when_the_goal_is_reached():
    lesson, said, _marked, cleared, watch = build([
        Step("click the wrench tab", target="the wrench tab"),
        Step("that is the modifier added", done=True),
    ])
    lesson.begin("add a modifier")
    watch.verdict = Clicked.ON_TARGET
    lesson.tick()

    assert said[-1] == "that is the modifier added"
    assert not lesson.active
    assert cleared.count("lesson-step") >= 1


def test_a_planner_that_cannot_decide_ends_the_lesson_rather_than_guessing():
    lesson, said, _marked, _cleared, _watch = build([None])

    assert lesson.begin("add a modifier")
    assert not lesson.active
    assert "cannot tell" in said[-1]


def test_being_lost_is_said_once_and_ends_it():
    lesson, said, _marked, _cleared, _watch = build([
        Step("i cannot see blender on your screen", lost=True),
    ])
    lesson.begin("add a modifier")

    assert said == ["i cannot see blender on your screen"]
    assert not lesson.active


def test_the_round_budget_stops_a_lesson_that_is_lost():
    lesson, said, _marked, _cleared, _watch = build([
        Step("click something", target="something"),
    ])
    lesson.begin("a never ending job")
    lesson.lesson.rounds = MAX_ROUNDS

    lesson._round()

    assert not lesson.active
    assert "as far as i can" in said[-1]


def test_stopping_is_obeyed_and_said():
    lesson, said, _marked, _cleared, _watch = build([
        Step("click the wrench tab", target="the wrench tab"),
    ])
    lesson.begin("add a modifier")

    assert lesson.answer("stop") is True
    assert not lesson.active
    assert "stopping" in said[-1]


def test_a_sentence_that_is_not_about_the_lesson_is_left_alone():
    lesson, _said, _marked, _cleared, _watch = build([
        Step("click the wrench tab", target="the wrench tab"),
    ])
    lesson.begin("add a modifier")

    assert lesson.answer("open notepad") is False
    assert lesson.active, "a question does not end a lesson"


def test_asking_for_it_again_repeats_the_step_without_a_model_call():
    lesson, said, _marked, _cleared, _watch = build([
        Step("click the wrench tab", target="the wrench tab"),
    ])
    lesson.begin("add a modifier")

    assert lesson.answer("say that again") is True
    assert said == ["click the wrench tab", "click the wrench tab"]
    assert lesson._teacher.steps == [], "nothing new was asked for"


def test_not_finding_a_target_still_leaves_the_instruction_said():
    """The words were right and the ring could not be placed. The
    instruction is most of the value, so it falls back to being told rather
    than apologising for the picture.
    """
    lesson, said, marked, _cleared, watch = build(
        [Step("click the wrench tab", target="the wrench tab")], found=False)
    lesson.locate = lambda what: None

    lesson.begin("add a modifier")

    assert said == ["click the wrench tab"]
    assert marked == []
    assert not watch.armed
    assert lesson.active, "and the lesson is still live"


# --- the click region --------------------------------------------------------

def test_an_exact_rectangle_accepts_a_click_inside_it():
    where = Where.around(FakeTarget(100, 100, 140, 120, is_exact=True))

    assert where.contains((120, 110))
    assert where.contains((100 - EXACT_PADDING, 100))
    assert not where.contains((100 - EXACT_PADDING - 1, 110))


def test_a_sighted_guess_accepts_a_click_within_its_measured_error():
    """52% exact and 61% within 50px, so the acceptance is a radius. A click
    40px from a guess that is itself 30px out is somebody hitting the thing
    we meant.
    """
    where = Where.around(FakeTarget(500, 500, 500, 500, is_exact=False))

    assert where.contains((500, 500))
    assert where.contains((500 + SIGHTED_RADIUS, 500))
    assert not where.contains((500 + SIGHTED_RADIUS + 2, 500))
    assert not where.exact


def test_a_sighted_region_is_bigger_than_an_exact_one():
    tight = Where.around(FakeTarget(100, 100, 110, 110, is_exact=True))
    loose = Where.around(FakeTarget(100, 100, 110, 110, is_exact=False))

    assert (loose.right - loose.left) > (tight.right - tight.left)


# --- the watcher itself ------------------------------------------------------

def test_a_button_already_down_when_the_step_begins_is_not_their_answer():
    """`GetAsyncKeyState` accumulates, and a step must not be completed by a
    drag that started before it existed. The first read is discarded.
    """
    positions = [(120, 110)]
    down = [True, False]          # already held, then released
    watch = Watch(cursor=lambda: positions[0],
                  button=lambda: down.pop(0) if down else False,
                  foreground=lambda: "window")
    watch.arm(FakeTarget(100, 100, 140, 120))

    assert watch.tick() is Clicked.WAITING, "the held button is discarded"
    # Released - but the press belonged to before the step, so this is not
    # on target either.
    assert watch.tick() is Clicked.WAITING


def test_a_press_and_release_inside_the_ring_is_on_target():
    down = iter([False, True, False])
    watch = Watch(cursor=lambda: (120, 110),
                  button=lambda: next(down, False),
                  foreground=lambda: "window")
    watch.arm(FakeTarget(100, 100, 140, 120))

    assert watch.tick() is Clicked.WAITING      # settles
    assert watch.tick() is Clicked.WAITING      # pressed
    assert watch.tick() is Clicked.ON_TARGET    # released on it


def test_a_release_outside_the_ring_is_off_target():
    down = iter([False, True, False])
    where = iter([(0, 0), (120, 110), (900, 900)])
    watch = Watch(cursor=lambda: next(where, (900, 900)),
                  button=lambda: next(down, False),
                  foreground=lambda: "window")
    watch.arm(FakeTarget(100, 100, 140, 120))

    watch.tick()
    watch.tick()
    assert watch.tick() is Clicked.OFF_TARGET


def test_a_drag_that_leaves_the_ring_is_not_a_click_on_it():
    """Completing on the PRESS would advance the lesson in the middle of
    somebody moving a slider."""
    down = iter([False, True, True, True, False])
    # The cursor is only read on RELEASE, so this is where the drag ENDED -
    # which is the whole question: the press was on the thing and the
    # release is somewhere else.
    down_at_the_end = (900, 110)
    watch = Watch(cursor=lambda: down_at_the_end,
                  button=lambda: next(down, False),
                  foreground=lambda: "window")
    watch.arm(FakeTarget(100, 100, 140, 120))

    verdicts = [watch.tick() for _frame in range(5)]
    assert verdicts[-1] is Clicked.OFF_TARGET
    assert all(verdict is Clicked.WAITING for verdict in verdicts[:-1])


def test_another_window_coming_to_the_front_is_stepping_away():
    front = iter(["blender", "blender", "chrome"])
    watch = Watch(cursor=lambda: (120, 110), button=lambda: False,
                  foreground=lambda: next(front, "chrome"))
    watch.arm(FakeTarget(100, 100, 140, 120))

    assert watch.tick() is Clicked.WAITING
    assert watch.tick() is Clicked.STEPPED_AWAY


def test_a_disarmed_watch_costs_nothing():
    reads = []
    watch = Watch(cursor=lambda: reads.append("cursor"),
                  button=lambda: reads.append("button"),
                  foreground=lambda: reads.append("front"))

    for _frame in range(100):
        assert watch.tick() is Clicked.WAITING

    assert reads == [], "not one Win32 call while nothing is being waited for"


# --- the history, and what the harness is told -------------------------------

def test_the_harness_is_never_told_what_the_next_step_is():
    """The written lesson handed the model every remaining step under "do
    NOT say these yet" and it read one out. Here there is nothing to leak -
    nothing knows the next step until it is needed.
    """
    lesson = StepByStep(goal="add a modifier")
    lesson.record("click the wrench tab", "clicked")

    said = lesson.where_we_are()
    assert "add a modifier" in said
    assert "click the wrench tab" in said, "what they have done"
    assert "do not guess one" in said


@pytest.mark.parametrize("instruction, crammed", [
    ("click add modifier", False),
    ("open the properties editor and click the blue spanner tab", False),
    ("click add modifier, then choose subdivision surface, then set the "
     "levels to three and apply it", True),
])
def test_a_step_that_is_several_actions_is_recognised(instruction, crammed):
    assert Step(instruction).crammed is crammed


def test_a_click_step_with_nothing_named_is_not_watchable():
    """Arming on it would wait for a click that can never be matched."""
    assert Step("click it", target="", kind="click").wants_a_click is False
    assert Step("click it", target="ok", kind="click").wants_a_click is True


# --- a step that repeats itself means the click did nothing -----------------
#
# Seen live, three rounds running on an unchanged screen: "click Show
# command menu", clicked, "click Show command menu", clicked. The history
# said so plainly and it repeated anyway - because from the screen alone the
# same step genuinely is the right answer.


def test_a_repeated_step_is_sent_back_once_with_the_reason():
    lesson, said, _marked, _cleared, watch = build([
        Step("click show command menu", target="show command menu"),
        Step("click show command menu", target="show command menu"),
        Step("click colour theme", target="colour theme"),
    ])
    lesson.begin("turn on dark mode")
    watch.verdict = Clicked.ON_TARGET
    lesson.tick()

    assert said == ["click show command menu", "click colour theme"]
    assert lesson.active
    told = lesson._teacher.histories[-1]
    assert "did NOT change" in told, "the model is told the concrete thing"


def test_repeating_twice_is_a_click_that_does_not_work_and_is_said():
    """Not a planning mistake the second time - and the user is the only one
    who can do anything about it."""
    lesson, said, _marked, _cleared, watch = build([
        Step("click show command menu", target="show command menu"),
        Step("click show command menu", target="show command menu"),
        Step("click show command menu", target="show command menu"),
    ])
    lesson.begin("turn on dark mode")
    watch.verdict = Clicked.ON_TARGET
    lesson.tick()

    assert "does not seem to be doing anything" in said[-1]
    assert not lesson.active


def test_the_same_target_phrased_differently_still_counts_as_a_repeat():
    lesson, said, _marked, _cleared, watch = build([
        Step("click the wrench tab", target="the wrench tab"),
        Step("now click the wrench", target="the wrench tab"),
        Step("click add modifier", target="add modifier"),
    ])
    lesson.begin("add a modifier")
    watch.verdict = Clicked.ON_TARGET
    lesson.tick()

    assert said[-1] == "click add modifier"
