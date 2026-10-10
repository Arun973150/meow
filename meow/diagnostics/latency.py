"""Where a turn's seconds actually go.

    meow latency
    meow latency --only "open notepad"

The repo's own numbers say ~2.5s to the first spoken sentence, and a real act
turn has been observed at 16.3s to open Blender and 25.9s for "create a new
file". Both of those have been true and unexplained for the whole project,
because the parts were never timed separately - the breakdown in AGENTS.md is
the DIGEST and the ROUTER, which are 460ms and 1,214ms and are not where the
time is.

So this times a real turn, phase by phase, against the real models:

    digest        reading the window's controls
    context       the recipes, the manual, the live scene, the OCR
    round 1       the model deciding which tool to call
    tools         the tool running, and the verifier checking it
    round 2       the model saying what happened

**First token matters more than total.** A turn that speaks at 3s and
finishes at 9s feels like a conversation; one that is silent for 9s and then
says everything feels broken, and this project has already fixed that once by
streaming. So the report leads with time to the first spoken sentence.

**Nothing is stubbed.** A latency measurement against a fake model measures
the fake. These are real calls, which costs real money - a handful of cents -
and is the only way the number means anything.
"""

from __future__ import annotations

import statistics
import sys
import time
from dataclasses import dataclass, field

from langchain_core.callbacks import BaseCallbackHandler
# Sentences that exercise the paths that have been observed to be slow. Each
# one is harmless and undoable: this runs against the real desktop.
TURNS = [
    ("answer", "what is the capital of france"),
    ("show", "where is the minimise button"),
    ("act", "open notepad"),
    ("act", "press ctrl a"),
]

# How many times each sentence runs. Three, because `temperature=0` is not
# determinism in this project and one pass is a sample - the same reason
# `meow routing --repeat` exists.
ROUNDS = 1


@dataclass
class Phase:
    """One model call or tool call, and how long it took."""

    kind: str
    name: str
    seconds: float
    detail: str = ""


@dataclass
class Turn:
    sentence: str
    route: str
    digest_seconds: float = 0.0
    context_seconds: float = 0.0
    first_word_seconds: float = 0.0
    total_seconds: float = 0.0
    phases: list = field(default_factory=list)
    said: str = ""
    error: str = ""

    def model_seconds(self) -> float:
        return sum(p.seconds for p in self.phases if p.kind == "model")

    def tool_seconds(self) -> float:
        return sum(p.seconds for p in self.phases if p.kind == "tool")


class Stopwatch(BaseCallbackHandler):
    """A LangChain callback that times every model and tool call.

    The graph's own timings are the only honest source: `_drain` sees a
    stream of chunks and cannot tell where one model call ended and the next
    began, which is exactly the question being asked.

    ⚠ **It must SUBCLASS BaseCallbackHandler.** A duck-typed handler is
    accepted, then every hook raises `AttributeError: no attribute
    'ignore_chain'` - which LangChain catches and prints, so the run looks
    like it worked and every phase silently comes back zero. The first
    version of this reported "unaccounted 100%".
    """

    def __init__(self) -> None:
        self.phases: list = []
        self._started: dict = {}
        self._names: dict = {}
        self.first_token: float | None = None
        self.began = time.perf_counter()

    def reset(self) -> None:
        """Start a fresh turn. One handler is attached for the whole run, so
        the graph is wrapped once rather than re-wrapped per turn - wrapping
        a wrapped runnable each time compounds."""
        self.phases = []
        self._started = {}
        self._names = {}
        self.first_token = None
        self.began = time.perf_counter()

    def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs):
        self._started[run_id] = time.perf_counter()

    def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs):
        self._started[run_id] = time.perf_counter()

    def on_llm_new_token(self, token, *, run_id=None, **kwargs):
        if self.first_token is None and (token or "").strip():
            self.first_token = time.perf_counter() - self.began

    def on_llm_end(self, response, *, run_id=None, **kwargs):
        started = self._started.pop(run_id, None)
        if started is None:
            return
        rounds = sum(1 for p in self.phases if p.kind == "model")
        self.phases.append(Phase("model", f"round {rounds + 1}",
                                 time.perf_counter() - started))

    def on_llm_error(self, error, *, run_id=None, **kwargs):
        self.on_llm_end(None, run_id=run_id)

    def on_tool_start(self, serialized, input_str, *, run_id=None, **kwargs):
        self._started[run_id] = time.perf_counter()
        self._names[run_id] = (serialized or {}).get("name") or "tool"

    def on_tool_end(self, output, *, run_id=None, **kwargs):
        started = self._started.pop(run_id, None)
        name = self._names.pop(run_id, "tool")
        if started is None:
            return
        self.phases.append(Phase("tool", name, time.perf_counter() - started,
                                 str(output)[:44].replace(chr(10), " ")))

    def on_tool_error(self, error, *, run_id=None, **kwargs):
        self.on_tool_end(f"<{type(error).__name__}>", run_id=run_id)


def _time_one(harness, route: str, sentence: str) -> Turn:
    from ..desktop.uia import digest_foreground

    turn = Turn(sentence=sentence, route=route)

    started = time.perf_counter()
    try:
        digest = digest_foreground()
    except Exception as error:  # noqa: BLE001
        digest, turn.error = None, f"digest: {type(error).__name__}"
    turn.digest_seconds = time.perf_counter() - started

    harness.guiding = route == "show"
    watch = Stopwatch()

    began = time.perf_counter()
    said = []
    try:
        for piece in harness.answer(sentence, digest=digest,
                                    ):
            if isinstance(piece, str):
                if not said:
                    turn.first_word_seconds = time.perf_counter() - began
                said.append(piece)
            else:
                # A confirmation. Allowed, so the turn completes and the
                # second model round is actually measured - but recorded,
                # because waiting for a human is not latency.
                turn.phases.append(Phase("gate", "asked permission", 0.0))
                piece.allow()
    except Exception as error:  # noqa: BLE001
        turn.error = f"{type(error).__name__}: {error}"
    turn.total_seconds = time.perf_counter() - began
    turn.said = " ".join(said)[:70]
    turn.phases.extend(watch.phases)
    return turn


def main() -> int:
    from ..agent.harness import Harness
    from ..agent.memory import Memory
    from ..platform.dpi import enable_per_monitor_dpi_awareness

    only = ""
    if "--only" in sys.argv:
        at = sys.argv.index("--only")
        only = sys.argv[at + 1] if at + 1 < len(sys.argv) else ""

    enable_per_monitor_dpi_awareness()
    wanted = [(route, said) for route, said in TURNS
              if not only or only.lower() in said.lower()]
    if not wanted:
        print(f"\n  nothing matching {only!r}\n")
        return 1

    print(f"\n  latency - {len(wanted)} turns, real models, real desktop")
    print("  every action here is harmless and undoable\n")

    # Built the way `loop.py` builds it - ask_before_acting=False - or this
    # measures a configuration the app never uses. The middleware gate would
    # interrupt every act turn, and the per-tool risk policy is the real one:
    # `judge` PROCEEDS on "open notepad" because the user named it.
    harness = Harness(confirm=lambda question: True, memory=Memory(),
                      ask_before_acting=False)
    # Attached to the GRAPH, once, so a tool that builds its own model is
    # timed too - `look_up` does, and its query rewriter was once streamed to
    # the user by mistake. Wrapped once rather than per turn: re-wrapping an
    # already-wrapped runnable compounds the config.
    watch = Stopwatch()
    harness.agent = harness.agent.with_config({"callbacks": [watch]})

    turns = []
    for route, sentence in wanted:
        watch.reset()
        turn = _time_one(harness, route, sentence)
        turn.phases = [p for p in turn.phases if p.kind == "gate"] + watch.phases
        if watch.first_token is not None and not turn.first_word_seconds:
            turn.first_word_seconds = watch.first_token
        turns.append(turn)

        print(f"  {route:<7} {sentence!r}")
        print(f"          digest {turn.digest_seconds * 1000:>6.0f}ms   "
              f"first word {turn.first_word_seconds:>5.2f}s   "
              f"total {turn.total_seconds:>5.2f}s"
              + (f"   [{turn.error}]" if turn.error else ""))
        for phase in turn.phases:
            print(f"            {phase.kind:<6} {phase.name:<18} "
                  f"{phase.seconds:>6.2f}s  {phase.detail}")
        if turn.said:
            print(f"          said: {turn.said!r}")
        print(flush=True)

    print("  " + "-" * 68)
    good = [t for t in turns if not t.error]
    if good:
        print(f"  first word   median {statistics.median(t.first_word_seconds for t in good):>5.2f}s   "
              f"worst {max(t.first_word_seconds for t in good):>5.2f}s")
        print(f"  whole turn   median {statistics.median(t.total_seconds for t in good):>5.2f}s   "
              f"worst {max(t.total_seconds for t in good):>5.2f}s")
        model = sum(t.model_seconds() for t in good)
        tools = sum(t.tool_seconds() for t in good)
        digest = sum(t.digest_seconds for t in good)
        whole = sum(t.total_seconds for t in good) or 1.0
        print()
        print(f"  where it all went, across {len(good)} turns:")
        print(f"    the model   {model:>6.2f}s  ({model / whole:.0%})")
        print(f"    the tools   {tools:>6.2f}s  ({tools / whole:.0%})")
        print(f"    the digest  {digest:>6.2f}s  ({digest / whole:.0%})")
        print(f"    unaccounted {whole - model - tools:>6.2f}s  "
              f"({(whole - model - tools) / whole:.0%})")
    print()
    return 0
