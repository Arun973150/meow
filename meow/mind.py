"""The part that answers.

Phase 0.6 and 0.7: a transcript goes in, spoken sentences come out, and the
model can see the screen when the request needs it.

This is deliberately small and it is NOT the harness. Phase 1 replaces it with
`create_agent` and tools; until then this is a single call that talks, which is
roughly Clicky parity and enough to exercise the whole loop.

Two things here earn their complexity.

**Sentences are emitted as they complete, not when the reply finishes.**
ElevenLabs takes 230-500ms to first audio and the model takes a second or more
to finish writing. Speaking sentence one while sentence two is still being
written removes the second wait entirely. It is the single biggest
perceived-latency win available and it costs nothing but a chunker.

**The screen is attached by policy, not by habit.** See `vision.py`: a
full-screen high-detail image is 36,835 tokens and most requests need no image
at all. The model gets the cheapest view that can answer the question.

The prompt is most of the product. It is written for the ear - see the voice
conventions in AGENTS.md - and the rules in it are the same ones Clicky arrived
at, for the same reasons.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from openai import OpenAI

from .config import openai_api_key
from .platform.capture import ScreenShot
from .pointing import Point, describe_point_protocol, parse_point, strip_points
from .vision import ScreenContext, ScreenNeed

MODEL = "gpt-4o-mini"

# Short. Long replies are worse out loud than on a page, they cost output
# tokens, and nobody wants a paragraph read at them.
MAX_OUTPUT_TOKENS = 220

# Four turns. Clicky keeps ten; this keeps fewer on purpose. Every turn is
# recharged in full on the next call, so history is a recurring cost rather
# than a one-off, and four is enough for "what about that one" to resolve
# while a long conversation stays cheap and stays on topic.
MAX_HISTORY_TURNS = 4

SYSTEM_PROMPT = """You are a cat that lives on the user's Windows desktop. You \
can see their screen when they ask about it, and you help them use their \
computer.

How you talk, and these matter more than what you say:
- Write for the ear, not the eye. This is read aloud. No markdown, no lists, \
no headings, no emoji.
- All lowercase.
- One to three sentences. Usually one.
- Never say "simply" or "just". Nothing is simple to someone who is stuck.
- Never end on a yes or no question. Those are dead ends that make the user \
say "yes" and wait again. If you need something, ask for the thing itself.
- If a screenshot is attached, you ARE looking at their screen right now. \
Describe what you can actually see in it. Never claim you cannot see the \
screen when an image is attached - an earlier version of this prompt did \
exactly that, describing the screen accurately in the same breath as denying \
it could see anything.
- If the image is too coarse to make out a detail, say that the detail is too \
small to read, which is true and useful. Do not say you cannot see the screen.
- If no screenshot is attached and the answer depends on the screen, say \
plainly that you are not looking at it, and ask what is on it.
- Do not narrate what you are about to do. Do it, or answer.

You are warm and brief. You are not chirpy, and you do not apologise twice."""


STYLE_REMINDER = (
    "Reply in lowercase, one or two short sentences, written to be read aloud. "
    "No markdown. Do NOT end on a question the user can answer with yes or "
    "no, and that includes ones dressed up as requests: not 'can you see "
    "it?', not 'can you tell me what you are trying to do?', not 'is that "
    "what you meant?'. Ask for the thing itself, or say nothing."
)


@dataclass
class Turn:
    role: str
    text: str


# Sentence enders followed by whitespace or the end of the string. The lookbehind
# rejects the two cases that produce nonsense fragments in speech: a decimal
# point inside a number, and a single initial like "J. Smith".
_SENTENCE_END = re.compile(r"(?<!\d)(?<!\b[A-Z])([.!?])(\s+|$)")

# Abbreviations that end in a period and are not ends of sentences. Short list
# on purpose - a false split costs a slightly odd pause, a missed split costs
# nothing at all, so this does not need to be exhaustive.
_ABBREVIATIONS = ("mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "e.g.", "i.e.",
                  "etc.", "vs.", "approx.")


class SentenceChunker:
    """Turns a token stream into complete sentences as soon as they are complete.

    The whole point is to hand the first sentence to text to speech while the
    model is still writing the second. Anything that delays the first emission
    defeats the purpose, so this never waits for a lookahead token.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        """Add streamed text, return any sentences that are now complete."""
        self._buffer += text
        sentences: list[str] = []

        while True:
            match = _SENTENCE_END.search(self._buffer)
            if match is None:
                break

            end = match.end(1)
            candidate = self._buffer[:end].strip()
            lowered = candidate.lower()

            if any(lowered.endswith(abbreviation) for abbreviation in _ABBREVIATIONS):
                # Not a sentence end. Keep looking past it rather than
                # splitting "dr. smith" into two utterances.
                remainder = self._buffer[match.end():]
                later = _SENTENCE_END.search(remainder)
                if later is None:
                    break
                end = match.end() + later.end(1)
                candidate = self._buffer[:end].strip()

            if candidate:
                sentences.append(candidate)
            self._buffer = self._buffer[end:].lstrip()

        return sentences

    def flush(self) -> str:
        """Whatever is left when the stream ends, sentence-ending or not."""
        remaining = self._buffer.strip()
        self._buffer = ""
        return remaining


class Mind:
    """One model call, streamed, with the screen attached when it is needed."""

    def __init__(self, model: str = MODEL, api_key: str | None = None) -> None:
        self._client = OpenAI(api_key=api_key or openai_api_key())
        self.model = model
        self.history: list[Turn] = []
        self.screen = ScreenContext()
        self.last_error: str | None = None

        # AT MOST ONE image is ever in context. Two things depend on this.
        #
        # It is what makes the unchanged-screen case work at all: skipping a
        # new image only helps if the previous one is still there to look at.
        # The first version of this dropped the image and sent the words "the
        # screen has not changed", and the model - correctly, given what it
        # was handed - replied that it could not see the screen.
        #
        # And it stops images accumulating. Context is recharged on every call,
        # so ten turns each carrying their own screenshot would cost 28,330
        # tokens of history rather than 2,833.
        self._screen_message: dict | None = None
        # Where the model said the last thing was, in IMAGE pixels. Read by the
        # caller after answer() finishes, because the tag arrives at the end.
        self.last_point: Point | None = None
        self.last_shot: ScreenShot | None = None

    def _messages(self, transcript: str, shot: ScreenShot | None,
                  crop_around: tuple[int, int] | None,
                  always_see: bool) -> tuple[list[dict], int]:
        attachment = self.screen.attach(transcript, shot, crop_around)

        if always_see and attachment.need is ScreenNeed.NONE and shot is not None:
            # Explicit override, for demos and for checking what the model can
            # actually make out. Never the default: see invariant 9.
            attachment = self.screen.attach("look at this screen", shot)

        if attachment.data_url is not None:
            # A fresh image replaces the old one rather than joining it.
            self._screen_message = {
                "role": "user",
                "content": [
                    {"type": "text",
                     "text": f"[This is my {attachment.label} right now.]"},
                    {"type": "image_url", "image_url": {
                        "url": attachment.data_url, "detail": attachment.detail,
                    }},
                ],
            }
            self.last_shot = shot
        elif attachment.note is None and attachment.need is ScreenNeed.NONE:
            # A request with nothing to do with the screen. The old image stays
            # in context - dropping it would make the next follow-up pay for a
            # new one - but nothing new is attached.
            pass

        system = SYSTEM_PROMPT
        if self._screen_message is not None and self.last_shot is not None:
            # Only explain the pointing tag when there is an image to point
            # into. Describing a coordinate space the model cannot see invites
            # it to invent coordinates.
            system += describe_point_protocol(self.last_shot)

        messages: list[dict] = [{"role": "system", "content": system}]
        for turn in self.history[-MAX_HISTORY_TURNS * 2:]:
            messages.append({"role": turn.role, "content": turn.text})
        if self._screen_message is not None:
            messages.append(self._screen_message)
        messages.append({"role": "user", "content": transcript})
        # Restated immediately before the reply. The rules are in the system
        # prompt, but with a screenshot in between they were being ignored:
        # live, the cat ended two replies in a row on "can you see it?" and
        # "what is on your screen right now?", which the prompt forbids twice.
        messages.append({"role": "system", "content": STYLE_REMINDER})
        return messages, attachment.estimated_tokens

    def answer(self, transcript: str, shot: ScreenShot | None = None,
               crop_around: tuple[int, int] | None = None,
               always_see: bool = False) -> Iterator[str]:
        """Yield spoken sentences as the model writes them.

        The caller hands each sentence straight to the speaker. The generator
        must therefore never buffer the whole reply - the first sentence is the
        one that matters for latency.
        """
        self.last_error = None
        self.last_point = None
        messages, _ = self._messages(transcript, shot, crop_around, always_see)

        chunker = SentenceChunker()
        spoken: list[str] = []

        try:
            stream = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=MAX_OUTPUT_TOKENS,
                stream=True,
                # Usage arrives on the final chunk when asked for, which is the
                # only way to know what a streamed call actually cost.
                stream_options={"include_usage": True},
            )

            for chunk in stream:
                if chunk.usage is not None:
                    self.screen.budget.record(
                        chunk.usage.prompt_tokens, chunk.usage.completion_tokens
                    )
                if not chunk.choices:
                    continue
                piece = chunk.choices[0].delta.content
                if not piece:
                    continue
                for sentence in chunker.feed(piece):
                    cleaned = self._take_point(sentence)
                    if cleaned:
                        spoken.append(cleaned)
                        yield cleaned

            # The tag has no sentence terminator, so it sits in the buffer and
            # comes out here. Stripping it is what stops the cat reading
            # "point colon four five zero comma three hundred" out loud.
            tail = self._take_point(chunker.flush())
            if tail:
                spoken.append(tail)
                yield tail

        except Exception as error:  # noqa: BLE001 - surfaced, not swallowed
            self.last_error = f"{type(error).__name__}: {error}"
            return

        if spoken:
            self.history.append(Turn("user", transcript))
            self.history.append(Turn("assistant", " ".join(spoken)))

    def _take_point(self, text: str) -> str:
        """Record any coordinates in this fragment and return it speakable."""
        if not text:
            return ""
        point = parse_point(text)
        if point is not None:
            self.last_point = point
        return strip_points(text)
