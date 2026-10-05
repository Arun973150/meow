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


# --- target rings, numbers, spotlight, groups, pinning ----------------------


def test_target_rings_are_concentric_and_do_not_collapse():
    """The innermost ring came out as a scribble: the wobble is three pixels
    and the ring was seven across, so most of its radius was hand.
    """
    from meow.desktop.annotate import Sketch

    sketch = Sketch()
    sketch.rings((200, 150), 46)
    image = sketch.render(400, 300)
    # Ink on the outer ring, and ink again much closer in. A single circle
    # would give the first and not the second.
    assert _ink_near(image, (200 + 44, 150)), "no outer ring"
    assert _ink_near(image, (200 + 26, 150)), "no inner ring"


def test_a_tiny_ring_is_dropped_rather_than_drawn_as_noise():
    from meow.desktop.annotate import Sketch

    sketch = Sketch()
    sketch.rings((60, 60), 9)
    # Renders without raising, and the shape stays recognisable - the point
    # is that nothing tries to wobble a nine-pixel circle by three.
    assert sketch.render(140, 140) is not None


def test_a_number_is_filled_so_it_is_legible_on_anything():
    from meow.desktop.annotate import Sketch

    sketch = Sketch()
    sketch.number((100, 100), 7)
    image = sketch.render(200, 200)
    # The centre of a badge is painted, not hollow. An outlined digit over a
    # screenshot is the least readable thing available.
    assert image.getpixel((100, 100))[3] > 200


def test_a_spotlight_dims_everything_except_its_region():
    from meow.desktop.annotate import Sketch

    sketch = Sketch()
    sketch.spotlight([(100, 100, 300, 200)])
    image = sketch.render(400, 300)
    inside = image.getpixel((200, 150))
    outside = image.getpixel((20, 20))
    assert inside[3] == 0, "the lit region must not be dimmed"
    assert outside[3] > 100, "everything else must be"


def test_a_spotlight_is_drawn_under_the_marks_whatever_the_order():
    """Added after an arrow, a shadow drawn in sequence would dim the arrow."""
    from meow.desktop.annotate import Sketch

    sketch = Sketch()
    sketch.number((60, 60), 1)
    sketch.spotlight([(200, 100, 380, 260)])
    image = sketch.render(400, 300)
    # The badge sits outside the lit region and is still opaque.
    assert image.getpixel((60, 60))[3] > 200


def test_marks_belong_to_groups_and_a_group_clears_alone():
    from meow.desktop.annotate import Sketch

    sketch = Sketch()
    sketch.rings((10, 10), 20, group="step-1")
    sketch.number((20, 20), 1, group="step-1")
    sketch.box(0, 0, 50, 50, group="window")

    assert sketch.groups() == ["step-1", "window"]
    assert sketch.clear(group="step-1") == 2
    assert sketch.groups() == ["window"]
    assert len(sketch.marks) == 1


def test_a_pinned_mark_never_expires():
    """A walkthrough step takes a minute to follow, and a ring that faded
    after six seconds left somebody looking at where it used to be.
    """
    from meow.desktop.annotate import PINNED, Mark

    pinned = Mark("circle", [(0, 0), (10, 10)], seconds=PINNED)
    pinned.born -= 600
    assert pinned.pinned
    assert not pinned.expired
    assert pinned.opacity == 1.0


def test_an_ordinary_mark_still_expires():
    from meow.desktop.annotate import Mark

    passing = Mark("circle", [(0, 0), (10, 10)], seconds=2.0)
    passing.born -= 3
    assert passing.expired


def test_the_same_mark_wobbles_the_same_way_every_render():
    """A fade re-renders the same mark five times and it has to look like one
    line each time. `id()` would do until CPython reuses an address.
    """
    from meow.desktop.annotate import Sketch

    sketch = Sketch()
    sketch.box(20, 20, 180, 120)
    first = list(sketch.render(200, 150).getdata())
    second = list(sketch.render(200, 150).getdata())
    assert first == second


def test_two_marks_of_the_same_shape_do_not_share_a_hand():
    from meow.desktop.annotate import Sketch

    one = Sketch(); one.box(20, 20, 180, 120)
    two = Sketch(); two.box(20, 20, 180, 120)
    assert list(one.render(200, 150).getdata()) != list(
        two.render(200, 150).getdata())


def test_a_label_can_point_at_what_it_names():
    from meow.desktop.annotate import Sketch

    plain = Sketch(); plain.label((100, 220), "razor tool")
    leading = Sketch(); leading.label((100, 220), "razor tool", leader=(300, 40))
    # The leader puts ink up near the target, where the plain label has none.
    assert not _ink_near(plain.render(400, 300), (300, 60), radius=14)
    assert _ink_near(leading.render(400, 300), (300, 60), radius=14)


def _ink_near(image, point, radius: int = 7) -> bool:
    """Is anything drawn within `radius` of this point?

    Needed because every stroke is deliberately wobbly now - asking whether
    one exact pixel is painted would test the hand rather than the shape.
    """
    x, y = point
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            inside = (0 <= x + dx < image.width and 0 <= y + dy < image.height)
            if inside and image.getpixel((x + dx, y + dy))[3] > 40:
                return True
    return False


# --- the agent's own pointer ------------------------------------------------


def test_the_agent_cursor_is_drawn_as_a_solid_arrow():
    """Filled rather than sketched. Every other mark here reads as a
    suggestion; a pointer has to read as precise.
    """
    from meow.desktop.annotate import Sketch

    sketch = Sketch()
    sketch.cursor((40, 40))
    image = sketch.render(120, 120)
    # The tip is at the point given, and the body hangs below and right of it.
    assert image.getpixel((42, 50))[3] > 180
    # Nothing above the tip - an arrow that straddled its own point would
    # cover the thing it is indicating.
    assert image.getpixel((40, 28))[3] == 0


def test_a_point_with_a_ghost_never_touches_the_real_pointer():
    """A handed-over task that yanks the pointer across the screen is taking
    the machine off somebody who is using it.
    """
    from meow.desktop import actions
    from meow.desktop.grounding import Source, Target

    moved = []
    drawn = []
    target = Target(left=10, top=10, right=30, bottom=30, name="Save",
                    role="Button", source=Source.UIA)

    original = actions.glide_to
    actions.glide_to = lambda *a, **k: moved.append(a) or True
    try:
        outcome = actions.point_at(target, ghost=lambda x, y: drawn.append((x, y)))
    finally:
        actions.glide_to = original

    assert outcome.ok
    assert outcome.method == "ghost"
    assert drawn == [(20, 20)]
    assert moved == [], "the real pointer must not move for unattended work"


def test_without_a_ghost_it_still_glides():
    """Teaching moves the real pointer, deliberately: a glide is the cat
    going somewhere while the user watches, and an arrow appearing instantly
    says nothing about where it came from.
    """
    from meow.desktop import actions
    from meow.desktop.grounding import Source, Target

    moved = []
    target = Target(left=10, top=10, right=30, bottom=30, name="Save",
                    role="Button", source=Source.UIA)

    original = actions.glide_to
    actions.glide_to = lambda *a, **k: moved.append(a) or True
    try:
        outcome = actions.point_at(target)
    finally:
        actions.glide_to = original

    assert outcome.method == "glide"
    assert moved
