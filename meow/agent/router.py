"""The reflex layer - Phase 1.6.

Jev answering a few small questions about what the user just said: what they
want, whether it needs the screen, whether getting it wrong would be hard to
undo.

**It runs while the user is still talking.** That is the entire point.
AssemblyAI emits interim transcripts mid-sentence, and each one is routed the
moment it arrives, so by the time `end_of_turn` fires the decision is usually
already made and costs nothing. Measured, a route takes ~421ms warm through the
Vercel gateway - which would be ruinous on the critical path and is free off it.

Only affordable because Jev is **non-generative**. It scores rather than writes,
so three questions in one request cost about what one costs.

**Never on the panic path.** Invariant 5: abort is local keyword matching with
no network in it. A reflex that needs a round trip is not a reflex.

Falls back to keywords when there is no key, and the fallback is honest about
being one - `Route.source` records which answered, so the evaluation never
conflates them. The gap is real: asked to "find three papers on solar costs and
put them in a spreadsheet", Jev returns `plan`, and keywords cannot.

See `meow/jev.py` for how it is reached, which took some finding.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum

from ..config import get
from ..desktop.vision import ScreenNeed, classify as classify_screen

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

# Built by concatenation rather than an escape, so the literal survives
# being written through a shell heredoc.
NEWLINE = chr(10)


class JevRouter:
    """Jev, asked three questions at once, through LangChain."""

    def __init__(self, api_key: str | None = None) -> None:
        from .jev import JevEvaluator, boolean, choice

        key = api_key or get("TYPESAFE_API_KEY")
        if not key:
            raise RuntimeError(
                "TYPESAFE_API_KEY is not set (Jev routing). "
                "Add it to .env, or Meow falls back to keyword routing."
            )

        self._evaluator = JevEvaluator(api_key=key)
        self._questions = {
            # criteria is a MAPPING of option to meaning, not a list of names -
            # the gateway rejects a list outright. It is the better shape
            # anyway: the difference between "show" and "act" is the whole
            # confirmation gate, and deserves a sentence rather than a label.
            "intent": choice(
                "What does the user want Meow to do?",
                {
                    "answer": ("A question about facts, or about the user's "
                               "own life, where being TOLD the answer is all "
                               "they want. If they want the answer PUT "
                               "somewhere - typed, written, saved - it is act "
                               "or plan, not answer."),
                    # "where is X" was landing on answer, because being TOLD
                    # where something is genuinely is an answer. It has to be
                    # show, or the reply is a description of where the button
                    # probably is instead of the cat going to it.
                    "show": ("Being SHOWN or TOLD, rather than having it "
                              "done. Where a thing is on their screen; how to "
                              "do something; a request to find, point at or "
                              "highlight. 'Where is the X', 'how do I X', "
                              "'how can I X', 'show me how to X', 'can you "
                              "take me to X'. If they want to LEARN it rather "
                              "than have it happen, it is show."),
                    # The boundary between these two is where the planner
                    # actually gets used, and it was in the wrong place: "open
                    # notepad and type hello there" scored as act, so the
                    # planner never ran for anything a person would call a
                    # multi-step task.
                    "act": ("ONE action they want CARRIED OUT. Press, "
                            "click, type, open or close something. "
                            "'Open notepad', 'click save', 'do it'. Asking HOW "
                            "to do a thing is show, not act - the difference "
                            "is whether they want it explained or done. "
                            "Read the conversation above: a sentence that "
                            "continues what Meow just did is act, even when "
                            "it names no application. After Meow opens "
                            "Notepad, 'write something about X' means write "
                            "it IN Notepad."),
                    "plan": ("THREE OR MORE actions, or anything that moves "
                             "between applications - open something then do "
                             "things in it, gather something then put it "
                             "somewhere. If the sentence contains 'and' "
                             "joining two different activities, it is a plan. "
                             "ALSO: producing a spreadsheet, document or deck "
                             "ABOUT a topic is always a plan, because the "
                             "information has to be found before it can be "
                             "written - 'gpu prices in india, put it in a "
                             "spreadsheet' is a plan even though the word "
                             "research was never said."),
                },
            ),
            "needs_screen": boolean(
                "Does answering this require looking at what is currently on "
                "the user's screen?"),
            "risky": boolean(
                "If this were carried out wrongly, would it change something "
                "the user would find hard to undo?"),
        }

    def route(self, text: str, partial: bool = False,
              context: str = "") -> Route:
        # The conversation goes in with the sentence. Without it every
        # sentence was classified alone, so "can you type about Elon Musk"
        # ten seconds after opening Notepad scored as a question ABOUT Elon
        # Musk - which is what it looks like, read by itself.
        state = (("Conversation so far:" + NEWLINE + context + NEWLINE
                  + "The user has just said: " + text)
                 if context else text)

        evaluation = self._evaluator.invoke({
            "state": state,
            "questions": self._questions,
        })

        try:
            intent = Intent(evaluation.pick("intent", "answer"))
        except ValueError:
            intent = Intent.ANSWER

        return Route(
            intent=intent,
            needs_screen=evaluation.flag("needs_screen"),
            risky=evaluation.flag("risky"),
            source="jev",
            milliseconds=evaluation.milliseconds,
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

    def __init__(self, use_jev: bool = True, memory=None) -> None:
        # The same Memory the harness and the answer path read. Routing was
        # the one decision still made with no idea what had happened before,
        # which is why a follow-up never resolved against the turn it
        # followed.
        self.memory = memory
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

    def _context(self) -> str:
        """Recent turns, or nothing when there is no memory to read."""
        return self.memory.recent() if self.memory is not None else ""

    def consider(self, partial_text: str) -> None:
        """Route an interim transcript in the background. Never blocks."""
        if not self.jev or len(partial_text.split()) < 3:
            # Two words is not enough to classify, and routing every keystroke
            # of a sentence spends requests to answer the same question.
            return

        # Read once, outside the worker. The memory is written from the
        # render loop, and a partial routed against context that changed
        # mid-flight is worse than one routed against slightly stale context.
        context = self._context()

        with self._lock:
            self._generation += 1
            generation = self._generation
        self.calls += 1

        def run() -> None:
            try:
                route = self.jev.route(partial_text, partial=True,
                                       context=context)
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
                return self.jev.route(final_text, context=self._context())
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
