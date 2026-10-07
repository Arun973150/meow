"""Grounding for windows the accessibility tree cannot see.

Blender draws its whole interface in OpenGL. UIA returns five elements -
Minimize, Maximize, Close, System, System - and not one menu, tool or panel.
Every strategy in this project up to now is blind there, and so is every
technique measured against it: Canny edge detection finds Blender's text
fields and misses the entire icon toolbar, because flat monochrome glyphs on a
flat dark background have almost no gradient.

What works is the one thing Clicky does and this project's ablation never
tested. Measured on a real Blender window, three for three:

    the Move tool in the left toolbar   -> (22, 170)   correct
    the Render menu at the top          -> (100, 36)   correct
    the Scale X field in the right panel-> (1203, 529) correct

**The tool definition is the mechanism, not the prompt.** Declaring the
`computer` tool activates coordinate-specific training; asking a
conversational model for `[POINT:x,y]` does not. That difference is why this
project's vision baseline scored 0/30 and this scores 3/3 on a harder window.
So the ablation's result should be read as "gpt-4o-mini emitting a text tag
cannot point", which is true, rather than "vision cannot point", which is not.

**This is the FALLBACK, never the default.** UIA is free, exact to the pixel,
returns a handle that can be invoked without moving the mouse, and answers in
268ms. This costs two API calls and seconds. It runs when `Regime.EMPTY` says
the tree holds nothing, and nowhere else.

Two API calls, because the tool insists on requesting its own screenshot
before it will click - there is no way to hand it one up front and have it act
on the first round.
"""

from __future__ import annotations

import base64
import io
import math
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx

from dataclasses import dataclass

from ..config import openai_api_key
from .grounding import Source, Target

# The cheap tier that supports the tool. gpt-4o-mini and the whole gpt-5 /
# gpt-4.1 / o-series family refuse it outright - measured, not assumed - so
# grounding is deliberately a different model from the rest of the app.
MODEL = "gpt-5.6-luna"

RESPONSES_URL = "https://api.openai.com/v1/responses"

# Generous. A grounding call happens while somebody is looking at their screen
# mid-walkthrough, not inside the sentence-to-speech path.
TIMEOUT_SECONDS = 120.0

# It asks for a screenshot, we hand one back, it clicks. A third is headroom
# for a model that looks twice; beyond that it is not going to commit.
MAX_ROUNDS = 3

# Half-width of the box built around the returned point. The model gives a
# point, and everything downstream wants a rectangle; this is small on purpose
# so a near miss reads as a miss rather than quietly overlapping the target.
POINT_RADIUS = 12

# When the search is narrowed to a region, the crop is enlarged to at least
# this on its longest side before being sent. Enlarging an image adds no
# information and it is still the whole point: the model's accuracy depends
# on how many pixels the target occupies in what it is shown, which is why
# zooming is the training-free technique that moves this number at all.
# Measured elsewhere at +13.4% on ScreenSpot-Pro for the same model.
ZOOM_TO = 1024

# Not beyond this, in either direction. Past about 4x a crop is mostly
# interpolation, and a very large image costs tokens for pixels that were
# invented on the way up.
MAX_ZOOM = 4.0

# Half-width of the crop taken round a first guess, in SCREEN pixels. The
# whole first pass is only trusted to be approximately right - measured, the
# misses cluster at a few hundred pixels rather than a few thousand - so the
# window has to be wide enough to contain the real target when the guess was
# off, and narrow enough that enlarging it is worth anything. 220 is about a
# sixth of a 2560-wide screen.
REFINE_RADIUS = 220

# And again, wider, when the first crop came back with nothing. MEGA-GUI calls
# this a recoverable search: the point is not to trust the first guess, and a
# refinement that can only ever narrow has no way back from a bad one.
RECOVER_RADIUS = 480

# Whether a plain `locate` refines by default. OFF, and that is a MEASURED
# result rather than caution.
#
# Looking twice was built on the published finding that zooming is the
# training-free way to improve GUI grounding - reported elsewhere at +13.4%
# on ScreenSpot-Pro. On this project's own 44 hand-labelled targets, with
# this model, it buys nothing:
#
#     tuned-on (24)   one pass 11/24    two passes 11/24
#     held out (20)   one pass 12/20    two passes 12/20
#     combined (44)   one pass 23/44    two passes 23/44     7s -> 16s
#
# The first run without the agreement gate was worse than net zero in both
# directions - Premiere 1/5 to 3/5 and Photoshop 1/5 to 2/5, against Chrome
# 6/6 to 4/6 and Resolve 2/2 to 1/2 - because a crop of a CORRECT answer
# gives the second pass a chance to pick the wrong neighbour. The gate fixed
# the losses; it did not produce a gain.
#
# The useful negative: **Illustrator, the worst application in the set, did
# not move at all** - 1/8 one pass, 1/8 two passes. Whatever is wrong there
# is not resolution, so a sharper picture cannot fix it, and neither will a
# bigger crop. That rules out a whole family of ideas cheaply.
#
# The code stays because the SAME path serves a region the user drew round,
# and that is a different signal: a person's circle is reliable where a
# model's first guess is not. `refine=True` turns it back on for anyone who
# wants to re-measure after a model change.
REFINE = False

# How far a second look may move the answer and still be believed, in SCREEN
# pixels. Measured, refinement helps where the first pass was bad and hurts
# where it was good: over 24 hand-labelled targets it took Premiere 1/5 to
# 3/5 and Photoshop 1/5 to 2/5, and took Chrome 6/6 down to 4/6 and Resolve
# 2/2 to 1/2. Net zero, at twice the latency.
#
# The pattern is one thing: a crop of a CORRECT answer gives the second pass
# a chance to pick the wrong neighbour. The recovery above only catches a
# pass that DECLINES, not one that confidently answers differently.
#
# So a refinement is believed when it SHARPENS and distrusted when it
# DISAGREES. A move of a few tens of pixels is the same target located
# better; a move of hundreds is a different object, and the coarse pass had
# the whole screen to judge by. Sixty-four is about one icon plus its
# spacing - a judgement, not a tuned constant, and checked against targets
# the threshold was not chosen on.
AGREEMENT_PIXELS = 64

# And a much tighter one for two looks at the SAME image, which is a different
# question wearing the same word. Above, a whole-screen guess is compared with
# a point derived from an enlarged crop - different scales, so the answer is
# EXPECTED to move a little and "better located" has to be allowed for. Here
# both looks are shown the identical picture at the identical size, so a
# genuine agreement is not approximate, it is the same pixel.
#
# Measured, and it matters. Over the 44 hand-labelled targets, when either
# look was right the two landed 0-6px apart in 24 of 26 cases. Sweeping it:
#
#     8px     draws 29 marks, 24 right,  5 wrong,  2 good points thrown away
#     64px    draws 31 marks, 24 right,  7 wrong,  2 good points thrown away
#     128px   draws 34 marks, 25 right,  9 wrong,  1 good point thrown away
#
# Eight is where it belongs: the same 24 right as sixty-four, with two fewer
# wrong marks. Reusing 64 here looked tidy and was simply the wrong number for
# the question - the two gates compare different things and have to differ.
SAME_POINT_PIXELS = 8

# Whether a plain `locate` takes TWO looks and only answers when they agree.
# ON, and measured on the same 44 hand-labelled targets as everything else.
#
# The failure here was never missing, it was missing CONFIDENTLY. One look
# answers 36 times out of 44 and is right 25, so eleven marks a session are
# drawn somewhere the user can see, on something they did not ask about, with
# nothing in the reply to suggest a guess. This project own rule says that is
# the worst outcome available: a miss is visible and recoverable, a wrong mark
# is neither.
#
# The model own instability is the signal. Two separate runs of the 44 flipped
# their verdict on SIX of them, and one target landed 6px out on one run and
# 545px out on the next. Where two looks land on the same spot it knows; where
# they land a screen apart it does not.
#
# Scored twice, because temperature=0 is not determinism here either:
#
#                             marks drawn      right        WRONG
#     one look                  36 / 37       25 / 21      11 / 16
#     two looks, must agree     29 / 29       24 / 21       5 /  8
#
# The absolute hit rate wanders by four between runs; the SHAPE reproduces
# exactly. Both times it drew eight fewer marks and lost at most one correct
# one, so what it declines is almost entirely what it was getting wrong.
# Precision 69% to 83% on one run, 57% to 72% on the other. It also rescued
# three, which was not the aim: the midpoint of two looks that agree is better
# than either. What it declines gets "i am not sure, circle it for me", which
# is the one signal measured to change this problem at all.
#
# **And it costs no time.** Asking twice normally doubles the latency, which
# is the reason not to - it does not here, because neither call depends on the
# other, so they go out together and the wall clock is the slower of the two
# rather than the sum. Measured: 7.2s median for the pair against 7.8s for
# one. It costs TOKENS, not seconds, which is the trade worth making for a
# mark drawn on somebody work.
LOOK_TWICE = True

# How many answers are remembered, and the whole reason for remembering them.
# Being asked where the same thing is twice on one screen is not an edge case
# - it is "say that again", "i cannot find it", and every re-point of a
# walkthrough step, and it cost a fresh eight seconds every time. Keyed on a
# fingerprint of the screen, so a screen that has moved on simply misses.
#
# The fingerprint is the perceptual one the answer path uses, not an exact
# hash: an exact hash reports a change on four of five captures of an idle
# screen, which would make the cache never hit at all. See
# `vision.changed_since`.
REMEMBERED_POINTS = 32


@dataclass
class Looked:
    """What one look found, carried rather than left on the object.

    Several looks run at once - `number_the_steps` asks about four things on
    one screen - and the outcome used to live in attributes reset at the top
    of `locate`, so concurrent calls clobbered each other and the caller read
    whichever happened to finish last. The attributes are still set for a
    single call, because the evaluation and the tests read them, but nothing
    concurrent may.
    """

    target: Target | None = None
    # The two looks landed in different places. NOT a miss: the thing is
    # there, and what is missing is which one. See `Harness.could_not_find`.
    disagreed: bool = False
    remembered: bool = False
    error: str | None = None
    passes: int = 0
    rounds: int = 0


class _Box:
    """A rectangle in screen coordinates, shaped like a drawn region.

    `_crop` takes anything with left/top/right/bottom, so the model's own
    first guess and a region the user circled go down exactly the same path.
    Spelled out rather than reusing `marking.Region`, which carries a
    timestamp and a tap flag that mean nothing here.
    """

    def __init__(self, left, top, right, bottom):
        self.left, self.top = int(left), int(top)
        self.right, self.bottom = int(right), int(bottom)


class ComputerUseGrounding:
    """Ask a model where something is, and let it answer by clicking."""

    def __init__(self, model: str = MODEL, api_key: str | None = None,
                 frozen=None, on_step=None,
                 look_twice: bool | None = None) -> None:
        self._model = model
        # Said aloud between passes. A single pass is about eight seconds and
        # a refined one is twenty-five, and silence is the thing this project
        # has fought hardest - it reads as stuck rather than as looking.
        self._on_step = on_step
        self._api_key = api_key or openai_api_key()
        # A fixed screenshot, for the evaluation. A live window moves between
        # strategies and the comparison stops being one.
        self._frozen = frozen
        self._look_twice = LOOK_TWICE if look_twice is None else look_twice
        # (screen fingerprint, description, region) -> the answer. A plain
        # dict in insertion order, trimmed from the front - least recently
        # ADDED rather than least recently used, which is the same thing when
        # every entry is from the last few seconds of one screen.
        self._remembered: dict = {}
        # Two looks write the counters and the error. See `_counted`.
        self._lock = threading.Lock()
        self.last_error: str | None = None
        self.rounds_used = 0
        # How many whole looks it took. One without refinement, two or three
        # with - worth separating from round trips, because the tool insists
        # on fetching its own screenshot, so each pass is two trips.
        self.passes = 0
        # Set when the two looks landed in different places. The caller says
        # it is not sure rather than drawing a mark, which is the point.
        self.disagreed = False
        self.remembered = False

    @property
    def name(self) -> str:
        return "computer-use"

    def locate(self, description: str, within=None,
               refine: bool | None = None) -> Target | None:
        """Where is this on screen? For ONE question at a time.

        Sets `passes`, `disagreed`, `remembered` and `last_error` on the
        object, which the evaluation and the tests read. Use `look` for
        anything concurrent - these attributes cannot describe four answers.
        """
        found = self.look(description, within, refine)
        self.passes = found.passes
        self.rounds_used = found.rounds
        self.disagreed = found.disagreed
        self.remembered = found.remembered
        self.last_error = found.error
        return found.target

    def look(self, description: str, within=None,
             refine: bool | None = None) -> Looked:
        """Where is this on screen? `within` narrows it to one region.

        A region is the single largest improvement available to this, and it
        is not a nicety. Measured over 44 hand-labelled targets this scored
        23, and the failures cluster where the screen is busiest - Illustrator
        1 of 8, Premiere 1 of 6 - because the search space is a whole
        professional interface. Cropping to a region and enlarging it turns
        "find the razor tool somewhere in Resolve" into "find it in this box",
        which is a different question.

        The region can come from the user drawing one, or - when `refine` is
        on and nobody drew anything - from the model's own first guess. See
        `_refined`.
        """
        # One `Looked` per call, so nothing about this answer is written
        # anywhere another concurrent call can see it.
        outcome = Looked()

        shot = self._frozen or (self._capture() or [None])[0]
        if shot is None:
            outcome.error = "no screenshot"
            return outcome

        key = self._key(shot, description, within)
        if key is not None:
            with self._lock:
                answer = self._remembered.get(key)
            if answer is not None:
                # Asked the same thing about the same screen. Eight seconds
                # and two model calls to re-derive an answer that cannot
                # have changed, which is most of what "say that again" used
                # to cost.
                outcome.remembered = True
                outcome.target = answer
                return outcome

        if within is None and (REFINE if refine is None else refine):
            outcome.target = self._refined(description, shot, outcome)
        elif self._look_twice:
            outcome.target = self._agreed(description, shot, within, outcome)
        else:
            outcome.target = self._one_pass(description, shot, within, outcome)

        if outcome.target is not None and key is not None:
            # Under the lock: several of these run at once on several
            # threads, and a length check followed by a pop and an insert is
            # not one operation.
            with self._lock:
                while len(self._remembered) >= REMEMBERED_POINTS:
                    self._remembered.pop(next(iter(self._remembered)))
                self._remembered[key] = outcome.target
        return outcome

    # --- shared between the two looks ---------------------------------------
    #
    # Two passes run on two threads, so everything they both write to goes
    # through here. `passes += 1` is a read, an add and a write, and two
    # threads interleaving them lose a count - which would not break an
    # answer but would make the latency report quietly wrong, and a
    # measurement nobody can trust is the one thing this project cannot
    # afford.

    def _counted(self, outcome, passes: int = 0, rounds: int = 0) -> None:
        with self._lock:
            outcome.passes += passes
            outcome.rounds += rounds

    def _failed(self, outcome, why: str) -> None:
        """Record why a look came back empty. The FIRST one wins.

        First rather than last, because the two looks can fail differently -
        one declining and one timing out - and the earlier failure describes
        what happened rather than what happened next.
        """
        with self._lock:
            if outcome.error is None:
                outcome.error = why

    def _key(self, shot, description: str, within):
        """What identifies this question about this screen, or None.

        None for a frozen shot: the evaluation replays one screenshot against
        several strategies, and a cache would answer the second one out of
        the first one pocket.
        """
        if self._frozen is not None:
            return None
        from .vision import changed_since

        try:
            _changed, fingerprint = changed_since(None, shot.image)
        except Exception:  # noqa: BLE001 - no fingerprint, no cache
            return None
        region = None if within is None else (
            within.left, within.top, within.right, within.bottom)
        return (fingerprint, " ".join(description.lower().split()), region)

    @staticmethod
    def _capture():
        from ..platform.capture import capture_screens

        try:
            return capture_screens()
        except Exception:  # noqa: BLE001 - a blind turn is not a crash
            return None

    def _agreed(self, description: str, shot, within,
                outcome) -> Target | None:
        """Two looks at once, and an answer only where they land together.

        Both calls go out together, so this is the latency of one look rather
        than two - see LOOK_TWICE for the measurement and for what it buys.

        A disagreement is not an error and not a miss: it is the model not
        knowing, which it has no other way of saying. The caller says so out
        loud rather than drawing a mark on a guess.
        """
        # DECODED HERE, on this thread, before either look touches it. PIL
        # loads lazily, so two threads calling `convert` on a freshly opened
        # image both drive the decoder and the second finds the file object
        # already closed: "NoneType has no attribute read", raised from
        # inside `_ask` with nothing in it about threads.
        #
        # A live capture is built in memory and already loaded, so this never
        # bit the voice path - it bit `meow evaluate --labelled`, which opens
        # saved screenshots, and it would bite anything handed an
        # `Image.open`.
        try:
            shot.image.load()
        except Exception:  # noqa: BLE001 - not every shot is a PIL image
            pass

        with ThreadPoolExecutor(max_workers=2) as pool:
            both = [pool.submit(self._one_pass, description, shot, within,
                                outcome) for _ in range(2)]
            first, second = (task.result() for task in both)

        if first is None or second is None:
            # One of them declined. A decline is a real answer - "it is not
            # there" - and it is not evidence for the other one point.
            if first is None and second is None:
                return None
            outcome.disagreed = True
            self._failed(outcome, "one look found it and the other did not")
            return None

        apart = math.dist(first.centre, second.centre)
        if apart > SAME_POINT_PIXELS:
            outcome.disagreed = True
            outcome.error = f"two looks landed {apart:.0f}px apart"
            return None

        # The midpoint. Two looks at the same icon landing a few pixels apart
        # average to a better estimate than either, and the radius is the
        # same POINT_RADIUS either way.
        x = (first.centre[0] + second.centre[0]) // 2
        y = (first.centre[1] + second.centre[1]) // 2
        return Target(left=x - POINT_RADIUS, top=y - POINT_RADIUS,
                      right=x + POINT_RADIUS, bottom=y + POINT_RADIUS,
                      name=description, role="", source=Source.VISION)

    def _refined(self, description: str, shot, outcome) -> Target | None:
        """Look once at the whole screen, then again at a crop of the guess.

        The first answer is treated as approximately right rather than right,
        which is what the measurements say it is: across 44 hand-labelled
        targets this was exact 23 times, within 50px 27 and within 100px 30 -
        so the gap between "roughly there" and "on it" is most of what is
        missing. A second look at a crop, enlarged, is the training-free way
        to close it.

        **A refinement that can only narrow has no way back from a bad first
        guess.** So the second pass may decline, and when it does the search
        widens and tries once more; if that declines too, the first answer
        stands. Never worse than one pass, which is the property that makes
        this safe to turn on by default.
        """
        first = self._one_pass(description, shot, outcome=outcome)
        if first is None:
            return None

        if self._on_step is not None:
            self._on_step("i think i see it - looking closer.")

        centre = first.centre
        for radius in (REFINE_RADIUS, RECOVER_RADIUS):
            box = _Box(centre[0] - radius, centre[1] - radius,
                       centre[0] + radius, centre[1] + radius)
            closer = self._one_pass(description, shot, within=box,
                                    outcome=outcome)
            if closer is None:
                continue
            moved = math.dist(closer.centre, centre)
            if moved <= AGREEMENT_PIXELS:
                # It found the same thing, more precisely. That is the whole
                # point of looking twice.
                return closer
            # It found something ELSE. Inside a crop everything looks like a
            # candidate, and the coarse pass had the whole screen to judge
            # by - so the coarse pass wins. This is the difference between
            # net zero and a real gain.
            outcome.error = f"the second look disagreed by {moved:.0f}px"
        return first

    def _one_pass(self, description: str, shot, within=None,
                  outcome=None) -> Target | None:
        """One look at one screenshot.

        The shot is handed IN rather than captured here, which matters for
        more than tidiness: two looks that each captured their own would be
        looking at two different moments, so an honest disagreement and the
        user having moved something would be indistinguishable.
        """
        outcome = outcome if outcome is not None else Looked()
        self._counted(outcome, passes=1)
        image = shot.image
        # Where the image sent to the model sits in the full screenshot, and
        # how much bigger it was made. Both are identity when nothing narrows
        # the search, which keeps the mapping below one expression.
        offset = (0, 0)
        zoom = 1.0
        if within is not None:
            cropped = self._crop(shot, within)
            if cropped is None:
                self._failed(outcome, "the marked region is off screen")
                return None
            image, offset, zoom = cropped

        point = self._ask(image, description, narrowed=within is not None,
                          outcome=outcome)
        if point is None:
            return None

        # Back out through every transform in the order they were applied:
        # undo the zoom, put the crop's origin back, undo the capture scale,
        # then add the monitor's own origin - which is NEGATIVE for a display
        # to the left of the primary, and forgetting it lands every answer on
        # the wrong screen.
        in_shot = (offset[0] + point[0] / zoom, offset[1] + point[1] / zoom)
        x = shot.monitor.left + int(in_shot[0] / shot.scale)
        y = shot.monitor.top + int(in_shot[1] / shot.scale)
        return Target(left=x - POINT_RADIUS, top=y - POINT_RADIUS,
                      right=x + POINT_RADIUS, bottom=y + POINT_RADIUS,
                      name=description, role="", source=Source.VISION)

    @staticmethod
    def _crop(shot, region):
        """The region as an enlarged image, plus how to map back out of it.

        Returns (image, offset_in_shot, zoom), or None when the region does
        not overlap this screenshot at all - which happens whenever somebody
        marks something on a monitor other than the one captured, and is a
        thing to report rather than to silently search the wrong screen for.
        """
        width, height = shot.image.size
        # Screen coordinates to image coordinates: drop the monitor's origin,
        # then apply the capture scale.
        left = int((region.left - shot.monitor.left) * shot.scale)
        top = int((region.top - shot.monitor.top) * shot.scale)
        right = int((region.right - shot.monitor.left) * shot.scale)
        bottom = int((region.bottom - shot.monitor.top) * shot.scale)

        left, top = max(0, left), max(0, top)
        right, bottom = min(width, right), min(height, bottom)
        if right - left < 8 or bottom - top < 8:
            return None

        patch = shot.image.crop((left, top, right, bottom))
        longest = max(patch.size)
        zoom = min(MAX_ZOOM, max(1.0, ZOOM_TO / longest))
        if zoom > 1.0:
            from PIL import Image

            patch = patch.resize(
                (int(patch.width * zoom), int(patch.height * zoom)),
                Image.LANCZOS)
        return patch, (left, top), zoom

    # --- the loop -----------------------------------------------------------

    def _ask(self, image, description: str, narrowed: bool = False,
             outcome=None) -> tuple[int, int] | None:
        outcome = outcome if outcome is not None else Looked()
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        width, height = image.size

        # Said plainly when the image is a crop, because otherwise the model
        # is being shown a fragment of an application and told it is a screen,
        # and "it is not on the screen" becomes the obvious answer to a
        # question about a toolbar it can see half of.
        where = ("A close-up of part of a Windows screen, {w}x{h}, is in "
                 "front of you. The user drew round this area themselves, so "
                 "what they are asking about is in it."
                 if narrowed else
                 "A {w}x{h} Windows screen is in front of you.")

        messages = [{"role": "user", "content": [{
            "type": "input_text",
            "text": (where.format(w=width, h=height)
                     + f" Take a screenshot, then click: {description}. "
                       f"If it is not there, say so instead of clicking "
                       f"anywhere."),
        }]}]

        for _ in range(MAX_ROUNDS):
            self._counted(outcome, rounds=1)
            reply = self._post(outcome, {
                "model": self._model,
                "tools": [{"type": "computer"}],
                "input": messages,
                "truncation": "auto",
            })
            if reply is None:
                return None
            output = reply.get("output", [])
            call = next((item for item in output
                         if item.get("type") == "computer_call"), None)
            if call is None:
                # It answered in words, which is what it does when the thing
                # is not there. A refusal is a real answer and is not an error.
                self._failed(outcome, "it did not find it")
                return None

            # `actions`, a LIST - not the singular `action` the older shape
            # used. Reading the wrong key returns None forever and looks
            # exactly like a model that will not commit.
            for action in (call.get("actions") or []):
                if action.get("type") in ("click", "double_click", "move"):
                    x, y = action.get("x"), action.get("y")
                    if x is None or y is None:
                        continue
                    return int(x), int(y)

            messages = messages + output + [{
                "type": "computer_call_output",
                "call_id": call.get("call_id"),
                "output": {"type": "computer_screenshot",
                           "image_url": f"data:image/png;base64,{encoded}"},
            }]

        self._failed(outcome, "never committed to a point")
        return None

    def _post(self, outcome, body: dict) -> dict | None:
        """One request. Never raises - a grounding miss is not a crash."""
        try:
            response = httpx.post(
                RESPONSES_URL,
                headers={"Authorization": f"Bearer {self._api_key}",
                         "Content-Type": "application/json"},
                json=body, timeout=TIMEOUT_SECONDS)
        except Exception as error:  # noqa: BLE001
            self._failed(outcome, f"{type(error).__name__}: {error}")
            return None
        if response.status_code != 200:
            self._failed(
                outcome, f"HTTP {response.status_code}: {response.text[:160]}")
            return None
        return response.json()
