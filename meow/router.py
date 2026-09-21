"""The reflex layer - Phase 1.6.

Jev, through `langchain-typesafe`, answering a few small questions about what
the user just said: is this about the screen, does it want an action, is it
risky, is it worth planning.

**It runs while the user is still talking.** That is the entire point. AssemblyAI
emits interim transcripts mid-sentence, and each one is routed the moment it
arrives, so by the time `end_of_turn` fires the decision is usually already made
and costs nothing. Routing after the sentence finishes would add its latency to
the critical path; routing during it adds none.

This is only affordable because Jev is **non-generative**. It classifies rather
than writes, so several questions asked together cost about what one costs, and
a wrong answer degrades gracefully - the harness still sees the screen, it just
pays for a screenshot it might not have needed.

**Never on the panic path.** Invariant 5: abort is local keyword matching with
no network in it. A reflex that needs a round trip is not a reflex.

Falls back to keyword matching when there is no key, so the loop runs without
one. The fallback is honest about being a fallback - `Route.source` says which
answered, and the evaluation reports them separately.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum

from .config import get
from .vision import ScreenNeed, classify as classify_screen

# Enough to be useful, few enough to stay one round trip. Jev charges roughly
# the same for several questions as for one, so the cost of adding a question is
# the thinking, not the tokens.
QUESTIONS_DOC = """
  intent      answer | show | act | plan
  needs_screen  does answering require looking at the screen
  risky         would getting this wrong change something hard to undo
"""


class Intent(Enum):
    ANSWER = "answer"   # talk, no screen, no action
    SHOW = "show"       # point at something, change nothing
    ACT = "act"         # press, type, open - the confirmed path
    PLAN = "plan"       # long enough to need steps (Phase 2)


@dataclass(frozen=True)
class Route:
    intent: Intent
    needs_screen: bool
    risky: bool
    source: str           # "jev" or "keywords"
    milliseconds: float = 0.0
    partial: bool = False  # routed from an interim transcript

    def describe(self) -> str:
        return (f"{self.intent.value}"
                f"{' +screen' if self.needs_screen else ''}"
                f"{' +risky' if self.risky else ''}"
                f" ({self.source}, {self.milliseconds:.0f}ms)")


# --- the fallback, which is also the panic-path classifier ------------------

_ACT_WORDS = (
    "click", "press", "open", "close", "type", "write", "save", "send",
    "delete", "run", "start", "stop", "select", "choose", "paste", "copy",
)
_SHOW_WORDS = ("where", "show", "point", "find", "which", "highlight")
_RISKY_WORDS = (
    "delete", "remove", "send", "post", "publish", "buy", "pay", "format",
    "uninstall", "overwrite", "replace", "close without", "discard",
)


def classify_locally(text: str) -> Route:
    """Keyword routing. No network, no key, no latency.

    Also what the panic path uses, because invariant 5 forbids a model call on
    the abort route and a local classifier is the only thing fast enough to be
    called a reflex.
    """
    started = time.perf_counter()
    lowered = text.lower()

    # Locating words are tested FIRST. "where is the save button" contains
    # "save", which is an action word, and the action test ran first - so
    # asking where something is routed to pressing it. Asking where a thing is
    # is never a request to operate it.
    if any(word in lowered for word in _SHOW_WORDS):
        intent = Intent.SHOW
    elif any(word in lowered for word in _ACT_WORDS):
        intent = Intent.ACT
    else:
        intent = Intent.ANSWER

    return Route(
        intent=intent,
        needs_screen=classify_screen(text) is not ScreenNeed.NONE,
        risky=any(word in lowered for word in _RISKY_WORDS),
        source="keywords",
        milliseconds=(time.perf_counter() - started) * 1000,
    )


# --- Jev --------------------------------------------------------------------

class JevRouter:
    """Jev via LangChain, asked several questions at once."""

    def __init__(self, api_key: str | None = None) -> None:
        from langchain_typesafe import Choice, Noul, TypeSafeClassifier

        key = api_key or get("TYPESAFE_API_KEY")
        if not key:
            raise RuntimeError(
                "TYPESAFE_API_KEY is not set (Jev routing, from typesafe.ai).\n"
                "  Add it to .env, or Meow falls back to keyword routing."
            )

        self._classifier = TypeSafeClassifier(api_key=key)
        self._questions = {
            # criteria is a MAPPING, not a list - each option carries its own
            # description. That is better than a bare list of names: the
            # difference between "show" and "act" is the whole confirmation
            # gate, and it deserves a sentence rather than a label.
            "intent": Choice(
                instructions="What does the user want Meow to do?",
                criteria={
                    "answer": "Be told something. No screen, no action.",
                    "show": ("Be shown where something is, without it being "
                             "pressed or changed."),
                    "act": ("Have something pressed, typed, opened or closed - "
                            "a change to the machine."),
                    "plan": ("A task with several steps that needs to be "
                             "worked through in order."),
                },
            ),
            "needs_screen": Noul(
                instructions=(
                    "Does answering this require looking at what is currently "
                    "on the user's screen?"
                ),
            ),
            "risky": Noul(
                instructions=(
                    "If this were carried out wrongly, would it change "
                    "something the user would find hard to undo?"
                ),
            ),
        }

    def route(self, text: str, partial: bool = False) -> Route:
        started = time.perf_counter()
        response = self._classifier.invoke({
            "state": text,
            "questions": self._questions,
        })

        try:
            intent = Intent(response.choices["intent"].choice)
        except (KeyError, ValueError, AttributeError):
            intent = Intent.ANSWER

        def noul(name: str) -> bool:
            try:
                return bool(response.nouls[name].noul)
            except (KeyError, AttributeError):
                return False

        return Route(
            intent=intent,
            needs_screen=noul("needs_screen"),
            risky=noul("risky"),
            source="jev",
            milliseconds=(time.perf_counter() - started) * 1000,
            partial=partial,
        )


class Router:
    """Routes speech as it arrives, so the answer is ready when it is needed.

    Call `consider()` on every interim transcript. Each one starts a routing
    call on a worker thread and the newest result wins; by the time the sentence
    ends, `latest` is usually already the right answer and `resolve()` returns
    immediately.

    Stale results are discarded by generation rather than by time. Interim
    transcripts arrive every few hundred milliseconds and routes can complete
    out of order, so "the most recent reply" and "the reply to the most recent
    text" are not the same thing.
    """

    def __init__(self, use_jev: bool = True) -> None:
        self.jev: JevRouter | None = None
        self.unavailable_reason: str | None = None

        if use_jev:
            try:
                self.jev = JevRouter()
            except Exception as error:  # noqa: BLE001 - falls back, never fatal
                self.unavailable_reason = str(error).splitlines()[0]

        self.latest: Route | None = None
        self.latest_text = ""
        self._generation = 0
        self._lock = threading.Lock()
        self.calls = 0
        self.wasted = 0  # routed a partial that the final made irrelevant

    @property
    def using_jev(self) -> bool:
        return self.jev is not None

    def consider(self, partial_text: str) -> None:
        """Route an interim transcript in the background. Never blocks."""
        if not self.jev or len(partial_text.split()) < 3:
            # Two words is not enough to classify, and routing every keystroke
            # of a sentence spends requests to answer the same question.
            return

        with self._lock:
            self._generation += 1
            generation = self._generation
        self.calls += 1

        def run() -> None:
            try:
                route = self.jev.route(partial_text, partial=True)
            except Exception:  # noqa: BLE001 - a failed guess is not an error
                return
            with self._lock:
                if generation != self._generation:
                    self.wasted += 1
                    return
                self.latest = route
                self.latest_text = partial_text

        threading.Thread(target=run, name="jev-route", daemon=True).start()

    def resolve(self, final_text: str, wait_seconds: float = 0.12) -> Route:
        """The route for a finished sentence.

        Waits briefly for an in-flight partial that is close enough to the final
        text to stand in for it - the last interim is usually the whole sentence
        minus punctuation. Beyond that it routes directly, or falls back.
        """
        deadline = time.perf_counter() + wait_seconds
        while time.perf_counter() < deadline:
            with self._lock:
                route, text = self.latest, self.latest_text
            if route is not None and _close_enough(text, final_text):
                return Route(route.intent, route.needs_screen, route.risky,
                             route.source, route.milliseconds, partial=False)
            time.sleep(0.01)

        if self.jev is not None:
            try:
                return self.jev.route(final_text)
            except Exception:  # noqa: BLE001
                pass
        return classify_locally(final_text)


def _close_enough(partial: str, final: str) -> bool:
    """Is a partial transcript the same sentence as the final one?

    Compared on words rather than characters, because the difference between an
    interim and a final turn is usually punctuation and capitalisation - which
    change the string completely and the meaning not at all.
    """
    if not partial:
        return False
    partial_words = partial.lower().split()
    final_words = final.lower().split()
    if not final_words:
        return False
    # The last interim is normally the full sentence. Accept it when it covers
    # most of the final text.
    return len(partial_words) >= max(3, int(len(final_words) * 0.8))
