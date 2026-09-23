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
