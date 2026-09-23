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


class ComputerUseGrounding:
    """Ask a model where something is, and let it answer by clicking."""

    def __init__(self, model: str = MODEL, api_key: str | None = None,
                 frozen=None) -> None:
        self._model = model
        self._api_key = api_key or openai_api_key()
        # A fixed screenshot, for the evaluation. A live window moves between
        # strategies and the comparison stops being one.
        self._frozen = frozen
        self.last_error: str | None = None
        self.rounds_used = 0

    @property
    def name(self) -> str:
        return "computer-use"

    def locate(self, description: str) -> Target | None:
        from ..platform.capture import capture_screens

        self.last_error = None
        self.rounds_used = 0
        shot = self._frozen or (capture_screens() or [None])[0]
        if shot is None:
            self.last_error = "no screenshot"
            return None

        point = self._ask(shot, description)
        if point is None:
            return None

        # The model answers in IMAGE pixels and the screen is somewhere else
        # entirely. `scale` is image -> screen as a DIVISION, and the monitor's
        # own origin goes back on top - a second monitor starts at a negative
        # x, and forgetting that lands every click on the primary display.
        x = shot.monitor.left + int(point[0] / shot.scale)
        y = shot.monitor.top + int(point[1] / shot.scale)
        return Target(left=x - POINT_RADIUS, top=y - POINT_RADIUS,
                      right=x + POINT_RADIUS, bottom=y + POINT_RADIUS,
                      name=description, role="", source=Source.VISION)

    # --- the loop -----------------------------------------------------------

    def _ask(self, shot, description: str) -> tuple[int, int] | None:
        buffer = io.BytesIO()
        shot.image.convert("RGB").save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        width, height = shot.image.size

        messages = [{"role": "user", "content": [{
            "type": "input_text",
            "text": (f"A {width}x{height} Windows screen is in front of you. "
                     f"Take a screenshot, then click: {description}. "
                     f"If it is not on the screen, say so instead of "
                     f"clicking anywhere."),
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
