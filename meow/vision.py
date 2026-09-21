"""When to send the model a screenshot, and how much of one.

Screenshots are the entire cost of this project. Text turns are nearly free;
images are not, and the difference between a careless policy and a careful one
is roughly thirty times the budget.

MEASURED on gpt-4o-mini, this account, a 1280x800 desktop:

    text only                        9 tokens
    detail=low    512px          2,833
    detail=low    768px          2,833
    detail=low   1280px          2,833
    detail=high   512px          8,500
    detail=high   768px         14,167
    detail=high  1280px         36,835

Two things fall out of that table, and the first is counter-intuitive enough
that it is worth stating plainly:

**At detail=low the cost is FLAT.** 512px and 1280px charge exactly the same
2,833 tokens. Downscaling an image to save money accomplishes nothing at low
detail - so send the biggest low-detail image available, because the extra
pixels are free.

**At detail=high the cost scales hard.** A full screen costs 36,835 tokens,
thirteen times a low-detail one. High detail is for reading small text, and when
it is genuinely needed the answer is to CROP rather than to send everything: a
512px crop at high detail is 8,500 tokens against 36,835 for the whole desktop,
and the crop is usually more relevant anyway.

So the policy, cheapest first:

1. Send nothing. Most requests do not need pixels at all.
2. Send the unchanged-screen marker. If the screen has not changed since the
   last image, say so in words instead of paying for the same pixels twice.
3. Send low detail, full size.
4. Send a high-detail crop, and only when something small has to be read.

Sending a full-screen high-detail image is not in the list. If that is ever the
right answer, it should be a deliberate override with a comment explaining why.
"""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass, field
from enum import Enum

from PIL import Image

from .platform.capture import ScreenShot

# gpt-4o-mini, USD per million tokens. Used only to report spend to the user,
# never to make a decision - a stale price should not silently change behaviour.
INPUT_COST_PER_MILLION = 0.15
OUTPUT_COST_PER_MILLION = 0.60

# Measured above, not documented anywhere by the provider in this form.
LOW_DETAIL_TOKENS = 2_833


class ScreenNeed(Enum):
    NONE = "none"
    LOW = "low"
    HIGH_CROP = "high_crop"


# Words that mean the request is ABOUT what is on screen. Deliberately biased
# toward false negatives: a missed screenshot produces "I cannot see that, can
# you describe it?", which costs a turn. A needless one costs 2,833 tokens
# every time and the user never finds out.
_SCREEN_WORDS = re.compile(
    r"\b("
    r"this|that|here|screen|window|page|tab|button|icon|menu|dialog|"
    r"see|look|showing|shown|displayed|visible|"
    r"click|press|select|open|close|where|which|highlight|point|"
    # Things that only exist on a screen. These belong here rather than in the
    # detail list below: "what does the label say" has no demonstrative and no
    # word like "screen", so without them it scored as needing no screen at all.
    r"label|error|dialog|toolbar|checkbox|field|popup|notification"
    r")\b",
    re.IGNORECASE,
)

# Requests to read something small. These ESCALATE an already-screen request to
# high detail; they do not decide on their own that the screen is involved.
# "read me a poem" must not trigger a screenshot, and it is the reason this is a
# separate test rather than more entries in the list above.
_FINE_DETAIL_WORDS = re.compile(
    r"\b(read|says?|written|text|label|number|error|message|code|exactly)\b",
    re.IGNORECASE,
)


def classify(transcript: str) -> ScreenNeed:
    """Decide how much screen this request needs.

    A keyword gate, not a model call. Spending a model call to decide whether
    to spend a model call is the kind of cleverness that costs more than it
    saves. Phase 1 replaces this with Jev, which is non-generative and
    effectively free - until then, words.
    """
    if not transcript.strip():
        return ScreenNeed.NONE

    mentions_screen = bool(_SCREEN_WORDS.search(transcript))
    if not mentions_screen:
        return ScreenNeed.NONE

    if _FINE_DETAIL_WORDS.search(transcript):
        return ScreenNeed.HIGH_CROP
    return ScreenNeed.LOW


@dataclass
class TokenBudget:
    """Running spend, so the number is visible before the credit runs out."""

    input_tokens: int = 0
    output_tokens: int = 0
    images_sent: int = 0
    images_skipped: int = 0

    def record(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.input_tokens += prompt_tokens
        self.output_tokens += completion_tokens

    @property
    def dollars(self) -> float:
        return (self.input_tokens * INPUT_COST_PER_MILLION / 1_000_000
                + self.output_tokens * OUTPUT_COST_PER_MILLION / 1_000_000)

    def summary(self) -> str:
        saved = self.images_skipped * LOW_DETAIL_TOKENS
        return (
            f"{self.input_tokens:,} in / {self.output_tokens:,} out "
            f"= ${self.dollars:.4f}   "
            f"images: {self.images_sent} sent, {self.images_skipped} skipped "
            f"(~{saved:,} tokens saved)"
        )


def _fingerprint(image: Image.Image, size: int = 16) -> bytes:
    """A cheap hash that changes when the picture does.

    Greyscale, shrunk to 16x16, thresholded at its own mean. Insensitive to a
    blinking cursor and a moving clock, sensitive to a window changing. Good
    enough to answer "is this the same screen?" without paying to find out.
    """
    small = image.convert("L").resize((size, size), Image.BILINEAR)
    pixels = list(small.getdata())
    average = sum(pixels) / len(pixels)
    bits = bytearray()
    for index in range(0, len(pixels), 8):
        byte = 0
        for offset, value in enumerate(pixels[index:index + 8]):
            if value > average:
                byte |= 1 << offset
        bits.append(byte)
    return bytes(bits)


def _difference(first: bytes, second: bytes) -> int:
    return sum(bin(a ^ b).count("1") for a, b in zip(first, second))


@dataclass
class ScreenAttachment:
    """What to actually put in the request, and what it cost."""

    need: ScreenNeed
    data_url: str | None = None
    detail: str = "low"
    note: str | None = None
    estimated_tokens: int = 0
    label: str = ""


class ScreenContext:
    """Decides what the model sees, and remembers what it has already seen."""

    # Out of 256 bits. Below this the screen is treated as unchanged; a cursor
    # blink and a clock tick land well under it.
    UNCHANGED_THRESHOLD = 8

    def __init__(self) -> None:
        self._last_fingerprint: bytes | None = None
        self.budget = TokenBudget()

    def attach(self, transcript: str, shot: ScreenShot | None,
               crop_around: tuple[int, int] | None = None) -> ScreenAttachment:
        """Build the cheapest attachment that can answer this request."""
        need = classify(transcript)
        if need is ScreenNeed.NONE or shot is None:
            return ScreenAttachment(need=ScreenNeed.NONE)

        fingerprint = _fingerprint(shot.image)
        unchanged = (
            self._last_fingerprint is not None
            and _difference(self._last_fingerprint, fingerprint)
            <= self.UNCHANGED_THRESHOLD
        )

        if unchanged and need is ScreenNeed.LOW:
            # Paying twice for identical pixels buys nothing - but only if the
            # previous image is still in context to look at. Mind keeps exactly
            # one and reuses it here. Dropping the image and describing it in
            # words instead does NOT work: the model, handed no picture, says
            # it cannot see the screen, which is the truth.
            self.budget.images_skipped += 1
            return ScreenAttachment(need=ScreenNeed.NONE)

        self._last_fingerprint = fingerprint

        if need is ScreenNeed.HIGH_CROP and crop_around is not None:
            image, detail, tokens = self._crop_high(shot, crop_around)
        else:
            # Full size at low detail: measured identical in cost to 512px, so
            # the extra pixels are free.
            image, detail, tokens = shot.image, "low", LOW_DETAIL_TOKENS

        self.budget.images_sent += 1
        return ScreenAttachment(
            need=need,
            data_url=_to_data_url(image),
            detail=detail,
            estimated_tokens=tokens,
            label=shot.label,
        )

    @staticmethod
    def _crop_high(shot: ScreenShot, around: tuple[int, int],
                   size: int = 512) -> tuple[Image.Image, str, int]:
        """A high-detail window around a screen point.

        512px is the sweet spot: one tile at high detail, 8,500 tokens against
        36,835 for the full desktop, and large enough to read a dialog.
        """
        centre_x, centre_y = shot.to_image(*around)
        half = size // 2
        left = max(0, min(centre_x - half, shot.image.width - size))
        top = max(0, min(centre_y - half, shot.image.height - size))
        box = (left, top,
               min(left + size, shot.image.width),
               min(top + size, shot.image.height))
        return shot.image.crop(box), "high", 8_500


def _to_data_url(image: Image.Image, quality: int = 80) -> str:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
