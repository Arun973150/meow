"""Windows the accessibility tree cannot see, and what happens there.

No network: the model call is replaced, so what is under test is the
classification and the coordinate arithmetic - which is where a pointing
system goes wrong silently.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from meow.desktop.computeruse import POINT_RADIUS, ComputerUseGrounding
from meow.desktop.grounding import Source
from meow.desktop.uia import Element, Regime, classify, only_chrome


def elements(*names):
    return [Element(name=n, role="Button", left=0, top=0, right=9, bottom=9)
            for n in names]


BLENDER = ("Minimize", "Maximize", "Close", "System", "System")


def test_a_window_showing_only_its_title_bar_is_empty():
    """Blender draws its interface in OpenGL, so UIA returns five elements and
    not one of them is Blender's. Counting those as content made classify say
    RICH - the worst possible answer, because a hybrid asking "can the tree
    see this?" is told yes and never falls through to vision. The cat was
    blind and did not know it.
    """
    assert only_chrome(elements(*BLENDER))
    assert classify(5, 5, 0.3, elements(*BLENDER)) is Regime.EMPTY


def test_a_real_window_is_still_rich():
    assert not only_chrome(elements("File", "Edit", "Save"))
    assert classify(3, 3, 0.3, elements("File", "Edit", "Save")) is Regime.RICH


def test_chrome_plus_one_real_control_is_not_blind():
    """One real control means the tree reaches the application."""
    mixed = elements("Minimize", "Close", "Save")
    assert not only_chrome(mixed)
    assert classify(3, 3, 0.3, mixed) is Regime.RICH


def test_a_slow_walk_is_still_truncated_not_empty():
    """A walk that gave up was not seen. Classifying it cost this project two
    days once already.
    """
    assert classify(5, 5, 99.0, elements(*BLENDER)) is Regime.TRUNCATED


# --- coordinate arithmetic ------------------------------------------------

@dataclass
class FakeMonitor:
    left: int
    top: int


@dataclass
class FakeImage:
    size: tuple

    def convert(self, _mode):
        return self

    def save(self, buffer, format=None):
        buffer.write(b"not-a-real-png")


@dataclass
class FakeShot:
    monitor: FakeMonitor
    image: FakeImage
    scale: float


def grounding_returning(point):
    found = ComputerUseGrounding(api_key="test")
    found._ask = lambda shot, description: point
    return found


def test_image_coordinates_become_screen_coordinates():
    """The model answers in IMAGE pixels. A 1920-wide screen captured at 1280
    has scale 0.667, so every coordinate is short by a third until it is
    divided back out.
    """
    found = grounding_returning((640, 400))
    found._frozen = FakeShot(FakeMonitor(0, 0), FakeImage((1280, 800)), 2 / 3)
    target = found.locate("middle")
    assert target is not None
    assert target.centre == (960, 600)


def test_a_second_monitor_origin_is_added_back():
    """Virtual desktop coordinates go negative left of the primary monitor.
    Forgetting the origin lands every click on the wrong display.
    """
    shot = FakeShot(FakeMonitor(-1920, 0), FakeImage((1280, 800)), 2 / 3)
    found = grounding_returning((640, 400))
    found._frozen = shot
    target = found.locate("middle")
    assert target.centre == (-1920 + 960, 600)


def test_a_refusal_is_not_a_target():
    """It says so in words when the thing is not on screen. Returning a box
    anyway would be a confident wrong answer, which is the failure this
    project cares about most.
    """
    found = ComputerUseGrounding(api_key="test")
    found._ask = lambda shot, description: None
    found._frozen = FakeShot(FakeMonitor(0, 0), FakeImage((1280, 800)), 1.0)
    assert found.locate("something absent") is None


def test_the_box_is_small_on_purpose():
    """A point becomes a rectangle, and a generous one would let a near miss
    read as a hit when this is scored.
    """
    found = grounding_returning((100, 100))
    found._frozen = FakeShot(FakeMonitor(0, 0), FakeImage((1280, 800)), 1.0)
    target = found.locate("thing")
    assert target.right - target.left == POINT_RADIUS * 2
    assert target.source is Source.VISION
    assert not target.is_exact
