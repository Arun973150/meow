"""Marks drawn on the screen for teaching.

Pure rendering: a PIL image in memory, no window, no screen.
"""

from __future__ import annotations

import time

import pytest

from meow.desktop.annotate import (
    DEFAULT_SECONDS, INK, Mark, Sketch, bezier, bowed,
)


def test_a_curve_is_sampled_not_faked():
    """PIL has no curve primitive. A bond path, a knight's arc or an arrow
    bending round a panel drawn as two straight segments looks like a mistake
    rather than a gesture.
    """
    points = bezier([(0, 0), (50, 100), (100, 0)], steps=20)
    assert len(points) == 21
    assert points[0] == (0, 0)
    assert points[-1] == (100, 0)
    # The middle must actually bulge, or it is a straight line with extra steps.
    assert points[10][1] > 20


def test_any_order_of_bezier_works():
    """De Casteljau, so the same code draws a quadratic, a cubic, or a
    six-point path somebody traced by hand.
    """
    for count in (2, 3, 4, 6):
        control = [(i * 10, i % 2 * 30) for i in range(count)]
        assert len(bezier(control, steps=8)) == 9


def test_a_bow_of_zero_is_a_straight_line():
    assert bowed((0, 0), (100, 0), 0.0) == [(0, 0), (100, 0)]


def test_a_bow_bends_perpendicular_and_reverses():
    """`bow` is how somebody would say it - "curve up from here to there" -
    rather than four control points, which a model asked for them produces
    nonsense for.
    """
    up = bowed((0, 0), (100, 0), 0.4)
    down = bowed((0, 0), (100, 0), -0.4)
    assert len(up) == 3 and len(down) == 3
    # Same midpoint, opposite sides of the line.
    assert up[1][1] * down[1][1] < 0


@pytest.mark.parametrize("make", [
    lambda s: s.arrow((10, 10), (200, 120), bow=0.3),
    lambda s: s.line((10, 10), (200, 120)),
    lambda s: s.curve([(0, 0), (60, 90), (140, 20), (200, 110)]),
    lambda s: s.path([(0, 0), (30, 40), (70, 10), (120, 80)]),
    lambda s: s.box(10, 10, 200, 120),
    lambda s: s.circle((100, 60), 40),
    lambda s: s.ellipse(10, 10, 200, 120),
    lambda s: s.highlight(10, 10, 200, 120),
    lambda s: s.label((20, 20), "look here"),
])
def test_every_kind_renders(make):
    sketch = Sketch()
    make(sketch)
    image = sketch.render(240, 160)
    assert image.size == (240, 160)
    # Something was actually drawn: some pixel is not fully transparent.
    assert any(pixel[3] > 0 for pixel in image.getdata())


def test_marks_expire_so_the_screen_does_not_fill():
    """A teaching overlay that accumulates is a window somebody has to clean
    up, and nobody ever does.
    """
    sketch = Sketch()
    sketch.circle((10, 10), 5, seconds=0.01)
    time.sleep(0.05)
    assert sketch.prune()
    assert sketch.empty


def test_a_mark_fades_rather_than_vanishing():
    mark = Mark("circle", [(0, 0), (5, 5)], seconds=1.0)
    assert mark.opacity == 1.0
    mark.born -= 0.9          # nine tenths through
    assert 0.0 < mark.opacity < 1.0


def test_coordinates_are_screen_not_window():
    """A second monitor to the left has NEGATIVE coordinates. Marks are given
    in screen space and the overlay's origin is subtracted, or every one lands
    on the primary display.
    """
    sketch = Sketch()
    sketch.box(-1900, 100, -1700, 200)
    image = sketch.render(1920, 1080, origin=(-1920, 0))
    assert any(pixel[3] > 0 for pixel in image.getdata())


def test_nothing_drawn_is_fully_transparent():
    assert all(pixel[3] == 0 for pixel in Sketch().render(40, 40).getdata())
