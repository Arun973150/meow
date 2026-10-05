"""The user pointing at their own screen.

Pure: cursor positions in, a region out. No Windows, no overlay, no mouse.

Spatial context is the one direction this project never had - everything else
runs cat-to-user. A circle is also the largest grounding signal available:
sight scored 23 of 44 hand-labelled targets and the failures cluster where the
screen is busiest, Illustrator 1 of 8 and Premiere 1 of 6, because the search
space is a whole professional interface.
"""

from __future__ import annotations

import pytest

from meow.desktop.marking import (
    MINIMUM_TRAVEL,
    PADDING,
    STALE_SECONDS,
    TAP_RADIUS,
    Marking,
    Region,
)


def drag(*points, release=True):
    """Drive a Marking through a drag and give back the finished session."""
    marking = Marking()
    marking.update(points[0], False)
    for point in points:
        marking.update(point, True)
    if release:
        marking.update(points[-1], False)
    return marking


def test_a_circle_becomes_the_box_around_it():
    marking = drag((100, 100), (200, 100), (200, 180), (100, 180), (100, 100))
    assert marking.finished
    region = marking.region
    assert region.left == 100 - PADDING
    assert region.top == 100 - PADDING
    assert region.right == 200 + PADDING
    assert region.bottom == 180 + PADDING
    assert not region.tapped


def test_a_tap_is_a_point_not_a_region():
    """A deliberate tap moves two or three pixels on a real trackpad.
    Claiming a three-pixel region would narrow the search to less than one
    icon, which is worse than not narrowing it at all.
    """
    marking = drag((400, 300), (402, 301))
    region = marking.region
    assert region.tapped
    # Centred on the last point KEPT - the second is three pixels away and
    # below MINIMUM_TRAVEL, which is the same filter that stops a drag
    # collecting eight hundred points.
    assert region.centre == (400, 300)
    assert region.width == TAP_RADIUS * 2


def test_points_closer_than_the_threshold_are_dropped():
    """A mouse reports far more detail than a freehand line needs, and a path
    with eight hundred points redraws every frame while it is being drawn.
    """
    marking = Marking()
    marking.update((0, 0), True)
    for step in range(1, 20):
        marking.update((step, 0), True)
    assert len(marking.points) < 20
    assert len(marking.points) >= 3


def test_a_second_press_starts_again():
    """Lifting and re-pressing is how somebody says "no, not that"."""
    marking = Marking()
    marking.update((10, 10), True)
    marking.update((90, 90), True)
    marking.update((90, 90), False)
    marking.finished = False          # as if the caller kept it alive
    marking._was_down = False
    marking.update((500, 500), True)
    assert marking.points == [(500, 500)]


def test_nothing_changes_while_the_button_is_up():
    marking = Marking()
    assert marking.update((10, 10), False) is False
    assert marking.update((99, 99), False) is False
    assert not marking.started
    assert not marking.finished


def test_cancelling_leaves_no_region():
    marking = Marking()
    marking.update((10, 10), True)
    marking.update((80, 80), True)
    marking.cancel()
    assert marking.finished
    assert marking.region is None


def test_a_region_goes_stale():
    """A region that outlives the question it was drawn for silently narrows
    the next one. Everything works, the answers are about the wrong part of
    the screen, and nothing in the reply says so.
    """
    region = Region(0, 0, 100, 100)
    assert not region.stale
    region = Region(0, 0, 100, 100, drawn=region.drawn - STALE_SECONDS - 1)
    assert region.stale


def test_a_region_knows_what_is_inside_it():
    region = Region(100, 100, 200, 200)
    assert region.contains((150, 150))
    assert not region.contains((250, 150))


def test_it_says_what_it_is_and_never_where_it_is():
    """The model repeats these back. "The area at 1200, 400" is a sentence
    nobody can act on, and the project's voice rules forbid coordinates.
    """
    for region in (Region(0, 0, 10, 10), Region(0, 0, 10, 10, tapped=True)):
        said = region.describe()
        assert not any(character.isdigit() for character in said)


# --- cropping to the region, which is where the value actually is -----------


class FakeMonitor:
    def __init__(self, left=0, top=0):
        self.left, self.top = left, top


class FakeShot:
    def __init__(self, image, scale=1.0, monitor=None):
        self.image = image
        self.scale = scale
        self.monitor = monitor or FakeMonitor()


def blank(width, height):
    from PIL import Image

    return Image.new("RGB", (width, height), (20, 20, 20))


def test_a_crop_is_enlarged_because_that_is_the_whole_point():
    """Enlarging adds no information and is still the technique: accuracy
    depends on how many pixels the target occupies in what the model is
    SHOWN. Measured elsewhere at +13.4% on ScreenSpot-Pro, training-free.
    """
    from meow.desktop.computeruse import ZOOM_TO, ComputerUseGrounding

    shot = FakeShot(blank(1920, 1080))
    patch, offset, zoom = ComputerUseGrounding._crop(
        shot, Region(400, 300, 600, 450))
    assert zoom > 1.0
    assert max(patch.size) >= min(ZOOM_TO, 200 * 4)
    assert offset == (400, 300)


def test_a_crop_is_clipped_to_the_screenshot():
    """People draw off the edge. A negative crop box raises in PIL and the
    region is still usable."""
    from meow.desktop.computeruse import ComputerUseGrounding

    shot = FakeShot(blank(800, 600))
    patch, offset, _zoom = ComputerUseGrounding._crop(
        shot, Region(-200, -200, 300, 250))
    assert offset == (0, 0)
    assert patch.size[0] > 0 and patch.size[1] > 0


def test_a_region_on_another_monitor_is_reported_rather_than_searched():
    """Marking something on a display the screenshot does not cover must not
    quietly search the wrong screen for it.
    """
    from meow.desktop.computeruse import ComputerUseGrounding

    shot = FakeShot(blank(800, 600))
    assert ComputerUseGrounding._crop(shot, Region(4000, 100, 4200, 300)) is None


@pytest.mark.parametrize("monitor_left, scale", [(0, 1.0), (-1920, 1.0),
                                                 (0, 2 / 3), (1920, 0.5)])
def test_a_point_in_a_crop_maps_back_to_the_right_place(monitor_left, scale):
    """Four transforms stacked: undo the zoom, add the crop origin, undo the
    capture scale, add the monitor origin. A second display to the LEFT of
    the primary starts at a negative x, and dropping it lands every answer on
    the wrong screen.
    """
    from meow.desktop.computeruse import ComputerUseGrounding

    found = ComputerUseGrounding(api_key="test")
    found._frozen = FakeShot(blank(1280, 800), scale,
                             FakeMonitor(monitor_left, 0))
    # Expressed on the SCREEN, which is where the user drew it - so a monitor
    # whose origin is -1920 has its regions at negative coordinates too.
    region = Region(monitor_left + 200, 150, monitor_left + 400, 300)

    # The model is asked about the crop and clicks its exact centre.
    def centre_of_the_crop(image, description, narrowed=False):
        assert narrowed, "a cropped image must say so, or 'not on the screen'"
        return (image.size[0] // 2, image.size[1] // 2)

    found._ask = centre_of_the_crop
    target = found.locate("the thing", within=region)
    assert target is not None, found.last_error

    # One image pixel is 1/scale screen pixels, so a half-scale capture
    # doubles any rounding on the way back out.
    slack = max(4, int(3 / scale))
    assert abs(target.centre[0] - region.centre[0]) <= slack, target.centre
    assert abs(target.centre[1] - region.centre[1]) <= slack, target.centre


def test_without_a_region_nothing_about_the_old_path_changes():
    from meow.desktop.computeruse import ComputerUseGrounding

    found = ComputerUseGrounding(api_key="test")
    found._frozen = FakeShot(blank(1280, 800), 1.0, FakeMonitor(0, 0))
    found._ask = lambda image, description, narrowed=False: (
        (640, 400) if not narrowed else (0, 0))
    target = found.locate("the thing")
    assert target.centre == (640, 400)
