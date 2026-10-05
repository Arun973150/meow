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

import httpx

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
                 frozen=None, on_step=None) -> None:
        self._model = model
        # Said aloud between passes. A single pass is about eight seconds and
        # a refined one is twenty-five, and silence is the thing this project
        # has fought hardest - it reads as stuck rather than as looking.
        self._on_step = on_step
        self._api_key = api_key or openai_api_key()
        # A fixed screenshot, for the evaluation. A live window moves between
        # strategies and the comparison stops being one.
        self._frozen = frozen
        self.last_error: str | None = None
        self.rounds_used = 0
        # How many whole looks it took. One without refinement, two or three
        # with - worth separating from round trips, because the tool insists
        # on fetching its own screenshot, so each pass is two trips.
        self.passes = 0

    @property
    def name(self) -> str:
        return "computer-use"

    def locate(self, description: str, within=None,
               refine: bool | None = None) -> Target | None:
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
        # Reset here rather than per pass, so a refined call reports the
        # round trips it ACTUALLY cost - which is the number worth watching.
        self.rounds_used = 0
        self.passes = 0
        if within is None and (REFINE if refine is None else refine):
            return self._refined(description)
        return self._one_pass(description, within)

    def _refined(self, description: str) -> Target | None:
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
        first = self._one_pass(description)
        if first is None:
            return None

        if self._on_step is not None:
            self._on_step("i think i see it - looking closer.")

        centre = first.centre
        for radius in (REFINE_RADIUS, RECOVER_RADIUS):
            box = _Box(centre[0] - radius, centre[1] - radius,
                       centre[0] + radius, centre[1] + radius)
            closer = self._one_pass(description, within=box)
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
            self.last_error = f"the second look disagreed by {moved:.0f}px"
        return first

    def _one_pass(self, description: str, within=None) -> Target | None:
        from ..platform.capture import capture_screens

        self.last_error = None
        self.passes += 1
        shot = self._frozen or (capture_screens() or [None])[0]
        if shot is None:
            self.last_error = "no screenshot"
            return None

        image = shot.image
        # Where the image sent to the model sits in the full screenshot, and
        # how much bigger it was made. Both are identity when nothing narrows
        # the search, which keeps the mapping below one expression.
        offset = (0, 0)
        zoom = 1.0
        if within is not None:
            cropped = self._crop(shot, within)
            if cropped is None:
                self.last_error = "the marked region is off screen"
                return None
            image, offset, zoom = cropped

        point = self._ask(image, description, narrowed=within is not None)
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

    def _ask(self, image, description: str,
             narrowed: bool = False) -> tuple[int, int] | None:
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
            self.rounds_used += 1
            reply = self._post({
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
                self.last_error = "it did not find it"
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

        self.last_error = "never committed to a point"
        return None

    def _post(self, body: dict) -> dict | None:
        """One request. Never raises - a grounding miss is not a crash."""
        try:
            response = httpx.post(
                RESPONSES_URL,
                headers={"Authorization": f"Bearer {self._api_key}",
                         "Content-Type": "application/json"},
                json=body, timeout=TIMEOUT_SECONDS)
        except Exception as error:  # noqa: BLE001
            self.last_error = f"{type(error).__name__}: {error}"
            return None
        if response.status_code != 200:
            self.last_error = f"HTTP {response.status_code}: {response.text[:160]}"
            return None
        return response.json()
