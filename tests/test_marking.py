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
    def centre_of_the_crop(image, description, narrowed=False, **_):
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
    found._ask = lambda image, description, narrowed=False, **_: (
        (640, 400) if not narrowed else (0, 0))
    target = found.locate("the thing", refine=False)
    assert target.centre == (640, 400)


# --- looking twice: coarse, then a crop of the guess -------------------------
#
# Measured over 24 hand-labelled targets, refining helped where the first pass
# was bad and hurt where it was good:
#
#     Premiere   1/5 -> 3/5     Chrome   6/6 -> 4/6
#     Photoshop  1/5 -> 2/5     Resolve  2/2 -> 1/2
#
# Net zero, at twice the latency. One cause: a crop of a CORRECT answer gives
# the second pass a chance to pick the wrong neighbour, and the recovery path
# only catches a pass that DECLINES. Hence the agreement gate.


def refining(answers):
    """Grounding whose passes return the given points, in order.

    Points are in the coordinate space of whatever image that pass was shown,
    which for a refinement is the enlarged crop - so "the centre of the crop"
    is the way to express an answer that agrees with the first guess.
    """
    from meow.desktop.computeruse import ComputerUseGrounding

    # The agreement gate is OFF here, deliberately: these cases are about
    # refinement, and two looks popping answers off one queue from two
    # threads would make the order - and so the expected answer - a race.
    # The gate has its own section below.
    found = ComputerUseGrounding(api_key="test", look_twice=False)
    found._frozen = FakeShot(blank(1280, 800), 1.0, FakeMonitor(0, 0))
    found.seen = []
    found.boxes = []
    queued = list(answers)

    def ask(image, description, narrowed=False, **_):
        found.seen.append((image.size, narrowed))
        wanted = queued.pop(0) if queued else None
        if wanted == "centre":
            return (image.size[0] // 2, image.size[1] // 2)
        if wanted == "corner":
            return (6, 6)
        return wanted

    real_crop = ComputerUseGrounding._crop

    def crop(shot, region):
        found.boxes.append((region.right - region.left,
                            region.bottom - region.top))
        return real_crop(shot, region)

    found._crop = crop
    found._ask = ask
    return found


def test_the_second_look_is_an_enlarged_crop_of_the_first_guess():
    from meow.desktop.computeruse import REFINE_RADIUS

    found = refining([(640, 400), "centre"])
    found.locate("the thing", refine=True)

    (first_size, first_narrowed), (second_size, second_narrowed) = found.seen
    assert first_narrowed is False, "the first look is the whole screen"
    assert second_narrowed is True, "the second must say it is a close-up"
    assert found.passes == 2
    assert max(second_size) > REFINE_RADIUS * 2, "a crop is enlarged, not cut"


def test_a_refinement_that_SHARPENS_is_believed():
    """A move of a few tens of pixels is the same target, located better.
    That is what looking twice is for.
    """
    found = refining([(640, 400), "centre"])
    target = found.locate("the thing", refine=True)
    assert target is not None
    # Within the agreement window of where the coarse pass pointed.
    assert abs(target.centre[0] - 640) < 64
    assert abs(target.centre[1] - 400) < 64


def test_a_refinement_that_DISAGREES_is_thrown_away():
    """Inside a crop everything looks like a candidate, and the coarse pass
    had the whole screen to judge by. This is the difference between net zero
    and a real gain: live, it took a HIT on a chess piece and moved it 544px.
    """
    found = refining([(640, 400), "corner", "corner"])
    target = found.locate("the thing", refine=True)
    assert target.centre == (640, 400), "the coarse answer must win"
    assert "disagreed" in (found.last_error or "")


def test_a_declined_crop_widens_the_search_before_giving_up():
    """A refinement that can only narrow has no way back from a bad first
    guess, which is the whole reason the first guess is being checked.
    """
    from meow.desktop.computeruse import RECOVER_RADIUS, REFINE_RADIUS

    found = refining([(640, 400), None, "centre"])
    found.locate("the thing", refine=True)
    assert found.passes == 3
    narrow, wide = found.boxes
    assert wide[0] > narrow[0], "the recovery pass must cover more screen"
    assert RECOVER_RADIUS > REFINE_RADIUS


def test_when_both_crops_decline_the_first_answer_stands():
    """Never worse than one pass. That is the property that makes this safe
    to turn on by default - "roughly there" beats nothing, and the caller is
    pointing rather than clicking.
    """
    found = refining([(640, 400), None, None])
    target = found.locate("the thing", refine=True)
    assert target is not None
    assert target.centre == (640, 400)


def test_a_first_pass_that_finds_nothing_is_not_refined():
    found = refining([None])
    assert found.locate("the thing") is None
    assert found.passes == 1


def test_a_region_the_user_drew_is_used_instead_of_guessing_one():
    """Somebody who circled the thing has given a better answer than the
    model's own first pass, so there is nothing to refine.
    """
    found = refining(["centre"])
    found.locate("the thing", within=Region(200, 150, 400, 300))
    assert found.passes == 1
    assert found.seen[0][1] is True


def test_refinement_is_off_by_default_and_that_is_a_measurement():
    """Not caution. On 44 hand-labelled targets, with this model, looking
    twice scored 23/44 against one pass's 23/44 and took 7s to 16s - and
    Illustrator, the worst application in the set, did not move at all,
    which rules out resolution as its problem.
    """
    from meow.desktop.computeruse import REFINE

    found = refining([(640, 400), "centre"])
    found.locate("the thing")
    assert REFINE is False
    # Asserted as "nothing was shown a crop" rather than "there was one
    # pass": the agreement gate takes two looks at the WHOLE screen by
    # default, which is a different thing entirely from refining.
    assert not any(narrowed for _size, narrowed in found.seen), (
        "no pass should have been shown a crop")


# --- two looks at once, and only answering where they agree -----------------
#
# The measured failure was never missing, it was missing CONFIDENTLY. On the
# 44 hand-labelled targets one look answers 36 times and is right 25, so
# eleven marks a session land on something the user did not ask about with
# nothing in the reply to suggest a guess - which this project's own rule
# calls the worst outcome available, since a miss is recoverable and a wrong
# mark is not.
#
#     one look                 25/44 right, and it points every time it answers
#     two looks, must agree    24/44 right
#
#     agreed and right          24   a mark worth drawing
#     agreed and wrong           7   the confident miss this does NOT catch
#     disagreed, both wrong     11   turned into "i am not sure"
#     disagreed, one was right   2   the cost: a good point thrown away
#
# And it costs no time: the two calls do not depend on each other, so they go
# out together. 7.2s median for the pair against 7.8s for one.


def looking_twice(first, second, shot=None):
    """Grounding whose two parallel looks return these two points.

    Keyed by WHICH THREAD asks rather than by call order, because two threads
    popping a queue is a race and a test that depends on one is worthless.
    """
    import threading

    from meow.desktop.computeruse import ComputerUseGrounding

    found = ComputerUseGrounding(api_key="test")
    found._frozen = shot or FakeShot(blank(1280, 800), 1.0, FakeMonitor(0, 0))
    handed_out = []
    lock = threading.Lock()

    def ask(image, description, narrowed=False, **_):
        with lock:
            handed_out.append(None)
            return first if len(handed_out) == 1 else second

    found._ask = ask
    return found


def test_two_looks_that_agree_answer_with_their_midpoint():
    """Two looks at the same icon landing a few pixels apart average to a
    better estimate than either, and nothing is drawn when they do not.
    """
    found = looking_twice((600, 400), (604, 402))
    target = found.locate("the thing")
    assert target is not None, found.last_error
    assert target.centre == (602, 401)
    assert found.passes == 2
    assert found.disagreed is False


def test_two_looks_a_screen_apart_answer_with_NOTHING():
    """Live, on this model: 6px out on one run and 545px on the next, for the
    same target. A ring drawn on either is a confident lie about the other.
    """
    found = looking_twice((600, 400), (1150, 180))
    assert found.locate("the thing") is None
    assert found.disagreed is True
    assert "apart" in (found.last_error or "")


def test_one_look_declining_is_not_evidence_for_the_other():
    """A decline is a real answer - "it is not there" - and it does not
    corroborate the point the other look committed to.
    """
    found = looking_twice((600, 400), None)
    assert found.locate("the thing") is None
    assert found.disagreed is True


def test_both_looks_declining_is_a_plain_miss_not_a_disagreement():
    """The caller says different things for the two, so they must not be
    reported the same way: "it is not on your screen" about something the
    user is looking at is the worst answer available.
    """
    found = looking_twice(None, None)
    assert found.locate("the thing") is None
    assert found.disagreed is False


def test_both_looks_see_the_SAME_screenshot():
    """Captured once and handed to both. Two looks that each grabbed their
    own would be looking at two moments, making an honest disagreement and
    the user having moved something indistinguishable.
    """
    found = looking_twice((600, 400), (600, 400))
    seen = []
    real = found._ask

    def ask(image, description, narrowed=False, **_):
        seen.append(id(image))
        return real(image, description, narrowed)

    found._ask = ask
    found.locate("the thing")
    assert len(seen) == 2 and seen[0] == seen[1]


def test_two_looks_at_one_image_must_agree_far_more_closely_than_a_crop():
    """The zoom refinement compares a whole-screen guess with a point derived
    from an enlarged crop - different scales, so the answer is EXPECTED to
    move a little and 64px allows for "better located". Two looks at the
    IDENTICAL picture have no such excuse.

    Measured: when either look was right the two landed 0-6px apart in 24 of
    26 cases, and the sweep says eight keeps the same 24 right as sixty-four
    with two fewer wrong marks. Reusing one number for both looked tidy and
    was the wrong number for the question.
    """
    from meow.desktop.computeruse import AGREEMENT_PIXELS, SAME_POINT_PIXELS

    assert SAME_POINT_PIXELS < AGREEMENT_PIXELS
    just_inside = looking_twice((600, 400), (600, 400 + SAME_POINT_PIXELS - 1))
    assert just_inside.locate("the thing") is not None
    just_outside = looking_twice((600, 400), (600, 400 + SAME_POINT_PIXELS + 2))
    assert just_outside.locate("the thing") is None
    # And the loose one would have accepted it, which is the point.
    assert SAME_POINT_PIXELS + 2 < AGREEMENT_PIXELS


# --- and not paying twice for the same question ------------------------------


def remembering(point):
    """Live grounding - no frozen shot - over a screen that does not move."""
    from meow.desktop.computeruse import ComputerUseGrounding

    found = ComputerUseGrounding(api_key="test")
    found.asked = 0
    # Structure, not a flat colour: the fingerprint thresholds a thumbnail at
    # its own mean, and every solid image thresholds to the same bits.
    from PIL import Image

    picture = Image.new("L", (64, 64))
    picture.putdata([(x * 7 + y * 13) % 256 for y in range(64)
                     for x in range(64)])
    shot = FakeShot(picture, 1.0, FakeMonitor(0, 0))

    def ask(image, description, narrowed=False, **_):
        found.asked += 1
        return point

    found._ask = ask
    found._capture = lambda: [shot]
    return found


def test_the_same_question_about_an_unchanged_screen_is_not_asked_twice():
    """"Say that again", "i cannot find it", and every re-point of a
    walkthrough step ask where the same thing is on the same screen. That
    cost a fresh eight seconds and two model calls every time.
    """
    found = remembering((600, 400))
    first = found.locate("the razor tool")
    asked_once = found.asked
    second = found.locate("the razor tool")
    assert first is not None and second is not None
    assert second.centre == first.centre
    assert found.asked == asked_once, "it should not have looked again"
    assert found.remembered is True


def test_a_different_question_about_the_same_screen_IS_asked():
    found = remembering((600, 400))
    found.locate("the razor tool")
    asked_once = found.asked
    found.locate("the blade tool")
    assert found.asked > asked_once
    assert found.remembered is False


def test_a_screen_that_moved_is_looked_at_again():
    from PIL import Image

    found = remembering((600, 400))
    found.locate("the razor tool")
    asked_once = found.asked

    moved = Image.new("L", (64, 64))
    moved.putdata([(x * 31 + y * 3) % 256 for y in range(64)
                   for x in range(64)])
    found._capture = lambda: [FakeShot(moved, 1.0, FakeMonitor(0, 0))]
    found.locate("the razor tool")
    assert found.asked > asked_once, "a changed screen must not be cached"


def test_a_frozen_screenshot_is_never_cached():
    """The evaluation replays one screenshot against several strategies, and
    a cache would answer the second out of the first one's pocket.
    """
    found = looking_twice((600, 400), (600, 400))
    asks = []
    real = found._ask

    def ask(image, description, narrowed=False, **_):
        asks.append(description)
        return real(image, description, narrowed)

    found._ask = ask
    found.locate("the thing")
    after_one = len(asks)
    found.locate("the thing")
    assert len(asks) > after_one, "a frozen shot must be looked at every time"
    assert found.remembered is False


def test_a_lazily_opened_screenshot_is_decoded_before_the_threads_see_it():
    """PIL loads on first use, so two threads calling `convert` on a freshly
    opened image both drive the decoder and the second finds the file object
    closed - "NoneType has no attribute read", raised from inside `_ask`
    with nothing in it about threads.

    A live capture is already in memory, so this never bit the voice path.
    It bit `meow evaluate --labelled`, which opens saved screenshots.
    """
    from meow.desktop.computeruse import ComputerUseGrounding

    class Undecoded:
        """PIL's lazy decode, modelled rather than raced.

        A real reproduction depends on two threads entering `convert` at the
        same moment, which is a flaky test and did not fire at all for a
        small picture. This asserts the invariant the fix actually
        establishes - loaded BEFORE either look is handed the image - and
        raises the error PIL really raised when it was not.
        """

        def __init__(self, real):
            self._real = real
            self.size = real.size
            self.loaded = False

        def load(self):
            self.loaded = True

        def convert(self, mode):
            if not self.loaded:
                raise AttributeError(
                    "'NoneType' object has no attribute 'read'")
            return self._real.convert(mode)

    found = ComputerUseGrounding(api_key="test")
    found._frozen = FakeShot(Undecoded(blank(320, 200)), 1.0,
                             FakeMonitor(0, 0))
    # Stubbed at the REQUEST, not at `_ask` - the decode happens inside
    # `_ask`, where the image is encoded to PNG, so replacing `_ask` would
    # skip the very thing under test. The first version of this did, and
    # passed happily with the fix taken out.
    found._post = lambda outcome, body: {"output": [{
        "type": "computer_call", "call_id": "x",
        "actions": [{"type": "click", "x": 160, "y": 100}]}]}
    target = found.locate("the thing")
    assert target is not None, found.last_error
    assert target.centre == (160, 100)


# --- several things at once ---------------------------------------------------
#
# `number_the_steps` asks about up to four things on one screen and
# `draw_a_move` about two. Each is two model round trips and about eight
# seconds, so four asked in turn is half a minute of silence with nothing
# appearing - and they are independent questions about one screenshot.


def harness_that_grounds(answers):
    """A Harness whose sight returns these answers, keyed by description.

    `answers` maps a description to a point, or to None for "not found", or
    to the string "unsure" for two looks that landed apart.
    """
    import threading
    import time

    from meow.agent.harness import Harness
    from meow.desktop.grounding import Source, Target

    harness = Harness.__new__(Harness)
    harness.digest = None
    harness.user_region = None
    harness.unsure_about = set()
    harness.asked = []
    harness.overlapped = False
    running = []
    lock = threading.Lock()

    class Eyes:
        def look(self, description, within=None):
            from meow.desktop.computeruse import Looked

            with lock:
                harness.asked.append(description)
                running.append(description)
                if len(running) > 1:
                    harness.overlapped = True
            # Long enough that sequential calls could not overlap by luck.
            time.sleep(0.05)
            with lock:
                running.remove(description)

            wanted = answers.get(description)
            if wanted == "unsure":
                return Looked(target=None, disagreed=True)
            if wanted is None:
                return Looked(target=None)
            x, y = wanted
            return Looked(target=Target(
                left=x - 2, top=y - 2, right=x + 2, bottom=y + 2,
                name=description, role="", source=Source.VISION))

    harness._eyes = lambda: Eyes()
    return harness


def test_several_things_are_grounded_at_the_SAME_TIME():
    harness = harness_that_grounds({"a": (10, 10), "b": (20, 20),
                                    "c": (30, 30)})
    found = harness.locate_several(["a", "b", "c"])
    assert len(found) == 3
    assert harness.overlapped, "they must not be asked one after another"


def test_the_answers_come_back_in_the_ORDER_ASKED():
    """`number_the_steps` draws the badges 1, 2, 3 - a reordered answer
    teaches the sequence wrong, which is worse than not numbering at all.
    """
    harness = harness_that_grounds({"first": (10, 10), "second": (20, 20),
                                    "third": (30, 30)})
    found = harness.locate_several(["first", "second", "third"])
    assert [target.centre for target in found] == [(10, 10), (20, 20),
                                                   (30, 30)]


def test_one_uncertain_answer_does_not_make_the_others_uncertain():
    """The grounding object's `disagreed` describes ONE answer. Four
    concurrent calls reading it would each get whichever finished last.
    """
    harness = harness_that_grounds({"clear": (10, 10), "muddled": "unsure",
                                    "absent": None})
    found = harness.locate_several(["clear", "muddled", "absent"])
    assert found[0] is not None and found[1] is None and found[2] is None
    assert harness.unsure_about == {"muddled"}
    # And the two empty answers are reported differently, which is the point.
    assert "circle" in harness.could_not_find("muddled")
    assert "circle" not in harness.could_not_find("absent")


def test_asking_for_one_thing_does_not_start_a_thread_pool():
    harness = harness_that_grounds({"a": (10, 10)})
    found = harness.locate_several(["a"])
    assert len(found) == 1 and not harness.overlapped
