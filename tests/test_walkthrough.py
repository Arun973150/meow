"""Being taught one step at a time, with somebody watching.

Pure: digests in, sentences out. No screen, no clock, no network.
"""

from __future__ import annotations

import pytest

from meow.agent.walkthrough import Progress, Walkthrough, from_directions
from meow.desktop.uia import Element, Regime, WindowDigest


def screen(title, *names):
    return WindowDigest(
        app="Settings", title=title,
        elements=[Element(name=n, role="Button", left=0, top=0,
                          right=9, bottom=9) for n in names],
        regime=Regime.RICH, total_found=len(names), usable_found=len(names),
        query_seconds=0.0)


def route():
    return Walkthrough(steps=["Settings", "Personalization", "Colors"])


def test_it_follows_somebody_through_the_whole_route():
    """The example that prompted this: "i don't know how to change dark mode
    to light mode". One step, wait, watch, next step.
    """
    walk = route()
    elsewhere = screen("Code", "Explorer", "Minimize")
    home = screen("Settings", "Settings", "System", "Personalization")
    personal = screen("Settings", "Personalization", "Background", "Colors")
    colours = screen("Settings", "Colors", "Mode", "Accent colour")

    assert walk.observe(elsewhere) is Progress.WAITING
    assert walk.observe(home) is Progress.ARRIVED
    assert walk.observe(home) is Progress.WAITING       # sitting still
    assert walk.observe(personal) is Progress.ADVANCED
    assert walk.index == 1
    assert walk.observe(personal) is Progress.WAITING
    assert walk.observe(colours) is Progress.FINISHED
    assert walk.finished


def test_a_page_listing_the_next_one_is_not_arriving_at_it():
    """The first version got this wrong. Windows Settings shows the page
    BELOW the one you are on, so "Personalization" and "Colors" are both
    visible the moment you reach Personalization - and announcing "you are
    there" then is two clicks early.
    """
    walk = route()
    walk.observe(screen("Settings", "Settings", "System", "Personalization"))
    progress = walk.observe(
        screen("Settings", "Personalization", "Background", "Colors"))
    assert progress is Progress.ADVANCED
    assert walk.current == "Personalization"
    assert not walk.finished


def test_sitting_still_says_nothing():
    """It never repeats itself at them. A walkthrough that re-reads the step
    every few seconds is a metronome, not help.
    """
    walk = route()
    home = screen("Settings", "Settings", "System", "Personalization")
    walk.observe(home)
    for _ in range(6):
        assert walk.say(walk.observe(home)) == ""


def test_somebody_who_skips_ahead_is_followed_not_corrected():
    """They knew part of the route. Sending them back to a step they already
    walked past is worse than saying nothing.
    """
    walk = route()
    walk.observe(screen("Code", "Explorer"))
    progress = walk.observe(screen("Settings", "Colors", "Mode", "Accent"))
    assert progress is Progress.FINISHED
    assert walk.current == "Colors"


def test_being_lost_is_said_once_not_every_poll():
    walk = route()
    elsewhere = screen("Code", "Explorer", "Minimize")
    spoken = [walk.say(walk.observe(elsewhere)) for _ in range(10)]
    assert len([line for line in spoken if line]) == 1
    assert "carry on" in "".join(spoken)


def test_a_single_step_is_not_a_walkthrough():
    """One step is a sentence, and the ordinary SHOW reply says it better."""
    class Directions:
        steps = ["Colors"]

    assert from_directions(Directions()) is None


def test_two_steps_are():
    class Directions:
        steps = ["Settings", "Colors"]

    walk = from_directions(Directions(), goal="dark mode")
    assert walk is not None and walk.remaining == 2


def test_names_are_matched_strictly():
    """These steps came off a web page. Under fuzzy matching everything
    exists, and a walkthrough that says "you are there" because something
    vaguely similar is on screen is worse than one that waits.
    """
    walk = Walkthrough(steps=["Settings", "Colors"])
    # "Settings" must not be satisfied by a control merely containing it.
    nearly = screen("Code", "Open Settings Sync Log", "Explorer")
    assert walk.observe(nearly) is Progress.WAITING
    assert not walk.started


# --- the watcher, with the screen faked -----------------------------------

def test_the_guide_walks_somebody_through_and_stops(monkeypatch):
    """The whole loop: begin, poll, speak a step when they act, finish.

    The screen is a list of frames rather than a real window, so this runs
    anywhere and takes milliseconds.
    """
    import meow.desktop.uia as uia_module
    from meow.app import guiding

    frames = [
        screen("Code", "Explorer", "Minimize"),
        screen("Settings", "Settings", "System", "Personalization"),
        screen("Settings", "Personalization", "Background", "Colors"),
        screen("Settings", "Colors", "Mode", "Accent colour"),
    ]
    handed_out = []

    def fake_digest():
        # Hold on the last frame once they are through them.
        index = min(len(handed_out), len(frames) - 1)
        handed_out.append(index)
        return frames[index]

    monkeypatch.setattr(uia_module, "digest_foreground", fake_digest)
    monkeypatch.setattr(guiding, "POLL_SECONDS", 0.01)

    said = []
    pointed = []

    class Directions:
        steps = ["Settings", "Personalization", "Colors"]

    guide = guiding.Guide(say=said.append,
                          point=lambda name, digest: pointed.append(name))
    assert guide.begin(Directions(), "change dark mode")

    deadline = __import__("time").time() + 5
    while guide.active and __import__("time").time() < deadline:
        __import__("time").sleep(0.01)

    assert said, "the guide never said anything"
    assert any("settings" in line for line in said)
    assert any("personalization" in line for line in said)
    assert any("you are there" in line for line in said)
    assert pointed, "it never pointed at a step"


def test_a_one_step_route_is_not_walked(monkeypatch):
    from meow.app import guiding

    class Directions:
        steps = ["Colors"]

    guide = guiding.Guide(say=lambda _: None)
    assert guide.begin(Directions()) is False
    assert not guide.active


def test_saying_anything_else_cancels_it():
    """A walkthrough is help, not a mode you have to escape."""
    from meow.app import guiding

    class Directions:
        steps = ["Settings", "Colors"]

    guide = guiding.Guide(say=lambda _: None)
    guide.begin(Directions())
    assert guide.active
    guide.cancel()
    assert not guide.active


# --- interrupted, and picked back up ----------------------------------------
#
# Every one of these was impossible before: the loop cancelled the walkthrough
# on ANY sentence, so "ok what next" destroyed the thing that knew what next
# was and the goal and the walked steps went with it.


def test_a_sentence_pauses_it_rather_than_ending_it():
    walk = route()
    home = screen("Settings", "Settings", "System", "Personalization")
    walk.observe(home)

    walk.pause()
    assert walk.paused
    assert not walk.finished
    # Paused means it says nothing even when handed a screen it would
    # otherwise narrate. Talking over somebody mid-sentence is worse than
    # waiting.
    personal = screen("Settings", "Personalization", "Background", "Colors")
    assert walk.observe(personal) is Progress.WAITING
    assert walk.index == 0


def test_resuming_does_not_send_them_back_to_the_start():
    """"Start with settings" to somebody who just asked what is next reads as
    the walkthrough having lost its place.
    """
    walk = route()
    walk.observe(screen("Settings", "Settings", "Personalization"))
    walk.pause()

    sentence = walk.resume()
    assert not walk.paused
    assert "settings" in sentence
    assert "start with" not in sentence


def test_it_remembers_every_step_already_walked():
    walk = route()
    walk.observe(screen("Settings", "Settings", "Personalization"))
    walk.observe(screen("Settings", "Personalization", "Colors"))
    assert walk.index == 1
    assert walk.walked == ["Settings"]

    walk.pause()
    walk.resume()
    # The pause changed nothing about where they had got to.
    assert walk.walked == ["Settings"]
    assert walk.current == "Personalization"


def test_skipping_ahead_still_records_what_was_passed():
    """Somebody who knew the route and walked it has walked those steps,
    whether or not the cat ever announced them.
    """
    walk = route()
    walk.observe(screen("Settings", "Settings", "Personalization"))
    # Straight to the end, skipping Personalization as a spoken step.
    assert walk.observe(screen("Settings", "Colors", "Mode")) is Progress.FINISHED
    assert "Personalization" in walk.walked


def test_stopping_says_what_was_covered():
    walk = route()
    walk.observe(screen("Settings", "Settings", "Personalization"))
    walk.observe(screen("Settings", "Personalization", "Colors"))

    said = walk.stopped()
    assert walk.finished
    assert "settings" in said


def test_asking_again_is_worded_differently_from_the_first_time():
    """It never repeats itself UNPROMPTED - that is nagging. Repeating when
    asked is the job, and hearing the identical sentence back is how a person
    concludes they are talking to a recording.
    """
    walk = route()
    walk.observe(screen("Settings", "Settings", "Personalization"))
    first = walk.say(Progress.ARRIVED)
    assert walk.said_again() != first
    assert "settings" in walk.said_again()


def test_a_route_longer_than_the_budget_never_claims_to_be_the_end():
    from meow.agent.walkthrough import MAX_STEPS

    class Directions:
        steps = [f"Step {n}" for n in range(MAX_STEPS + 6)]

    walk = from_directions(Directions())
    assert len(walk.steps) == MAX_STEPS
    assert walk.truncated
    walk.index = MAX_STEPS - 1
    ending = walk.say(Progress.FINISHED)
    assert "you are there" not in ending


def test_an_ordinary_route_is_not_marked_truncated():
    class Directions:
        steps = ["Settings", "Personalization", "Colors"]

    walk = from_directions(Directions())
    assert not walk.truncated
    assert walk.steps == ["Settings", "Personalization", "Colors"]


# --- the four sentences somebody says mid-route ------------------------------


@pytest.fixture
def guide(monkeypatch):
    """A Guide with its eyes and its threads removed.

    Watching needs UIA and a worker thread, and neither is what these cases
    are about: the question is which sentence does what to the route.
    """
    from meow.app import guiding

    said: list[str] = []
    pointed: list[str] = []
    watcher = guiding.Guide(say=said.append, point=lambda name, _d=None: None)
    monkeypatch.setattr(watcher, "_start_watching", lambda _w: None)
    monkeypatch.setattr(watcher, "_point_now",
                        lambda: pointed.append(watcher.walkthrough.current))
    watcher.walkthrough = route()
    watcher.said = said
    watcher.pointed = pointed
    return watcher


@pytest.mark.parametrize("said", [
    "ok what next", "continue", "done", "i did it", "next step",
    "then what", "carry on",
])
def test_carrying_on_keeps_the_route(guide, said):
    assert guide.answer(said) is True
    assert guide.walkthrough is not None
    assert not guide.walkthrough.finished


@pytest.mark.parametrize("said", ["i can't find it", "where is it", "i'm lost"])
def test_being_stuck_points_rather_than_talking(guide, said):
    """The step is right and the screen is not helping. Reading it out again
    is the one response that does not help.
    """
    assert guide.answer(said) is True
    assert guide.pointed == ["Settings"]


def test_asking_to_repeat_keeps_the_place(guide):
    assert guide.answer("say that again") is True
    assert guide.walkthrough.index == 0
    assert guide.said


def test_saying_stop_ends_it(guide):
    assert guide.answer("never mind") is True
    assert guide.walkthrough is None


@pytest.mark.parametrize("said", [
    "open notepad",
    "how do i change my dns",
    "what's the weather in delhi",
    "stop the music",
])
def test_a_real_request_is_not_the_walkthrough_s_business(guide, said):
    """False is the only path that ends a route by accident, which is why
    every branch above is an exact phrase rather than anything weighed.
    """
    assert guide.answer(said) is False


def test_nothing_is_claimed_when_there_is_no_route():
    from meow.app import guiding

    watcher = guiding.Guide(say=lambda _s: None)
    assert watcher.answer("continue") is False


# --- teaching a PROCEDURE, not a route --------------------------------------
#
# Live, asked to teach animating a bouncing ball in Blender, it said:
#
#   "first, add a UV sphere for the ball with shift a and select mesh, then
#    set a keyframe for its starting position by pressing i and choosing
#    location. to create the bouncing effect, move to a later frame, change
#    the sphere's position, and insert another keyframe."
#
# Four steps in two breaths. That is the recitation a walkthrough exists to
# replace, and after it the user still could not do the thing.


BOUNCE = ["press shift a and choose mesh, then uv sphere",
          "press i and choose location to set a keyframe",
          "move to frame twenty",
          "move the ball down and press i again"]


def test_a_procedure_is_said_one_step_at_a_time():
    from meow.agent.walkthrough import from_steps

    walk = from_steps(BOUNCE, doing=True)
    assert walk.doing
    assert "shift a" in walk.say(Progress.ARRIVED)
    # The other three are not in the first thing said.
    assert "frame twenty" not in walk.say(Progress.ARRIVED)


def test_a_doing_step_never_claims_to_point_at_itself():
    """"Press shift A" is an instruction, not a place. Saying "i am pointing
    at it" is a promise about the screen that nothing checked.
    """
    from meow.agent.walkthrough import from_steps

    walk = from_steps(BOUNCE, doing=True)
    assert "pointing at it" not in walk.say(Progress.ARRIVED)


def test_saying_you_did_it_moves_on():
    from meow.agent.walkthrough import from_steps

    walk = from_steps(BOUNCE, doing=True)
    walk.say(Progress.ARRIVED)
    assert walk.advance() is Progress.ADVANCED
    assert "keyframe" in walk.current
    walk.advance()
    walk.advance()
    # Four steps means four advances: being TOLD the last one is not having
    # done it, which is where this differs from a named route - there,
    # arriving at the last page is the end.
    assert walk.advance() is Progress.FINISHED
    assert walk.finished


def test_a_doing_walkthrough_watches_for_nothing():
    """There is no name on screen for "press shift a". Announcing progress
    because a window title changed would be guessing out loud.
    """
    from meow.agent.walkthrough import from_steps

    walk = from_steps(BOUNCE, doing=True)
    busy = screen("Blender", "Minimize", "Maximize", "Close")
    assert walk.observe(busy) is Progress.WAITING
    assert walk.index == 0


def test_one_step_is_not_a_walkthrough():
    from meow.agent.walkthrough import from_steps

    assert from_steps(["press ctrl n"], doing=True) is None


def test_reporting_what_you_did_is_heard_as_moving_on(guide):
    """"Yeah, I've added the ball now" routed to ANSWER and got "i can't see
    what you're talking about, tell me what's on your screen" - said to
    somebody it had just told to go and add a ball.
    """
    guide.walkthrough = None
    guide.teach(BOUNCE, "animate a bouncing ball")
    assert guide.walkthrough.doing

    assert guide.answer("yeah i've added the ball now") is True
    assert guide.walkthrough.index == 1
    assert any("keyframe" in said for said in guide.said)


def test_a_report_only_counts_while_a_lesson_is_live():
    """Outside one the same sentence is an ordinary request and must route
    normally. Being loose is only safe because there is a step on the table.
    """
    from meow.app import guiding

    watcher = guiding.Guide(say=lambda _s: None)
    assert watcher.answer("i've added the ball now") is False
