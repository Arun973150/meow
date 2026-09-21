"""Jev, through the Vercel AI Gateway, as a LangChain Runnable.

Jev is TypeSafe AI's System One *evaluation* model: it scores state against
typed questions and returns choices and probabilities. It is not a language
model, and the gateway says so if you treat it as one - `/v1/chat/completions`
answers with "Model 'typesafe-ai/jev' is an evaluation model, not a language
model."

The route that works is `POST /v1/evaluate` on the gateway, with a `vck_` key.
Worth writing down, because three plausible alternatives do not:

    api.typesafe.ai/v1/systemone   401  - wants a native typesafe.ai key
    gateway /v1/chat/completions   400  - evaluation model, wrong API
    gateway /v1/evaluation(s|…)    404  - the path is /v1/evaluate, singular

`langchain-typesafe` speaks to the first of those, so it cannot be used with a
gateway key. This is a Runnable instead, which is the LangChain-native way to
carry a service that has its own protocol - it composes with `RunnableParallel`,
which is how routing ends up overlapping the rest of the turn rather than
queueing behind it.

**Measured: ~810ms warm** for three questions in one request, from here through
the gateway. The 30-80ms in TypeSafe's own material is the model thinking, not a
round trip from another continent. 810ms would be ruinous on the critical path
and is free off it, which is the entire reason this runs on interim transcripts
while the user is still speaking.

Question types the gateway accepts, verified rather than assumed:

    boolean   {"type": "boolean", "instructions": ...}  -> probability
    choice    {"type": "choice", "instructions": ...,
               "criteria": {name: description, ...}}    -> choice + probabilities

`criteria` for a choice is a MAPPING, not a list of names - a list is rejected
with "expected record, received undefined".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.runnables import Runnable, RunnableConfig

GATEWAY_URL = "https://ai-gateway.vercel.sh/v1/evaluate"
MODEL = "typesafe-ai/jev"

# A boolean answer is a probability, not a yes. Half is the neutral line; the
# caller can look at the number when the difference matters.
BOOLEAN_THRESHOLD = 0.5

# Long enough for a slow round trip, short enough that a stuck request cannot
# hold up a turn. Routing is optional - a timeout falls back to keywords.
TIMEOUT_SECONDS = 8.0


def boolean(instructions: str) -> dict[str, Any]:
    return {"type": "boolean", "instructions": instructions}


def choice(instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    """A choice question. `criteria` maps each option to what it means."""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


@dataclass(frozen=True)
class Evaluation:
    """What Jev answered, plus what it cost."""

    answers: dict[str, Any]
    milliseconds: float
    input_tokens: int = 0
    output_tokens: int = 0

    def flag(self, name: str, threshold: float = BOOLEAN_THRESHOLD) -> bool:
        answer = self.answers.get(name) or {}
        return float(answer.get("probability", 0.0)) >= threshold

    def probability(self, name: str) -> float:
        answer = self.answers.get(name) or {}
        return float(answer.get("probability", 0.0))

    def pick(self, name: str, default: str = "") -> str:
        answer = self.answers.get(name) or {}
        return str(answer.get("choice", default))

    def confidence(self, name: str) -> float:
        answer = self.answers.get(name) or {}
        return float(answer.get("confidence", 0.0))


class JevEvaluator(Runnable):
    """LangChain Runnable wrapping Jev on the Vercel AI Gateway.

    Input is `{"state": ..., "questions": {...}}`, the same shape the AI SDK
    uses, so the TypeScript examples in Vercel's documentation translate
    directly. Output is an `Evaluation`.
    """

    def __init__(self, api_key: str, model: str = MODEL,
                 url: str = GATEWAY_URL,
                 timeout: float = TIMEOUT_SECONDS) -> None:
        self.api_key = api_key
        self.model = model
        self.url = url
        # One client, reused. A new connection per call would add a TLS
        # handshake to a request whose whole point is to be quick.
        self._client = httpx.Client(timeout=timeout, headers={
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        })

    def invoke(self, input: dict[str, Any],
               config: RunnableConfig | None = None,
               **kwargs: Any) -> Evaluation:
        started = time.perf_counter()
        response = self._client.post(self.url, json={
            "model": self.model,
            "state": input["state"],
            "questions": input["questions"],
        })
        response.raise_for_status()
        payload = response.json()
        usage = payload.get("usage") or {}

        return Evaluation(
            answers=payload.get("answers") or {},
            milliseconds=(time.perf_counter() - started) * 1000,
            input_tokens=int(usage.get("inputTokens", 0)),
            output_tokens=int(usage.get("outputTokens", 0)),
        )

    def close(self) -> None:
        self._client.close()
