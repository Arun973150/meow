"""The reflex layer - Phase 1.6.

A small model answering three questions about what the user just said: what
they want, whether it needs the screen, whether getting it wrong would be hard
to undo.

**It runs while the user is still talking.** That is the entire point.
AssemblyAI emits interim transcripts mid-sentence, and the growing sentence is
routed as it arrives, so by the time `end_of_turn` fires the decision is
usually already made and costs nothing on the critical path.

**This used to be Jev** - TypeSafe's System One evaluation model, reached
through the Vercel AI Gateway, which scored typed questions instead of writing
text. It was the right shape for the job and it is gone: no key, no service.
The replacement is `gpt-4.1-nano` with a strict JSON schema, the same small
model the query rewriter already uses and the cheapest thing in this project.
The criteria below are Jev's, word for word - they are the part that was
expensive to learn, and they describe the four intents rather than the model
that reads them.

One thing genuinely got worse in the swap and is handled rather than ignored.
Jev was **non-generative**, so three questions cost about what one cost and
routing every interim transcript was free. A chat model charges per call, and
a sentence emits five or six interims - so a partial is only routed once it
has grown by `MINIMUM_NEW_WORDS` since the last one. Without that, routing
alone would cost about as much as the rest of the turn.

**Never on the panic path.** Invariant 5: abort is local keyword matching with
no network in it. A reflex that needs a round trip is not a reflex.

Falls back to keywords when there is no key, and the fallback is honest about
being one - `Route.source` records which answered, so the evaluation never
conflates them. The gap is real: asked to "find three papers on solar costs
and put them in a spreadsheet", the model returns `plan`, and keywords cannot.

Measured by `meow routing`, which replays the sentences that were wrong once.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum

from ..config import get
from ..desktop.vision import ScreenNeed, classify as classify_screen

# The smallest model that can read a sentence, and the one already in use for
# query rewriting. A reasoning model is wrong here twice over: it spends a
# token budget thinking about a question that wants a reflex, and `gpt-5-nano`
# was measured returning one query instead of three for exactly that reason.
MODEL = "gpt-4.1-nano"

# A route is three fields. 200 is far more than the JSON needs and still small
# enough that a runaway reply cannot cost anything - and a truncated reply is
# worse than a slow one, because strict JSON that stops mid-string does not
# parse at all.
MAX_OUTPUT_TOKENS = 200

# Long enough for a slow round trip, short enough that a stuck request cannot
# hold up a turn. Routing is optional - a timeout falls back to keywords.
TIMEOUT_SECONDS = 8.0

# How much a partial transcript has to grow before it is worth routing again.
# Jev charged for thinking rather than tokens, so every interim could be
# routed; a chat model charges per call. Interims arrive every few hundred
# milliseconds and consecutive ones differ by a word, so routing all of them
# spends five requests to answer the same question five times.
MINIMUM_NEW_WORDS = 3

# Below this there is nothing to classify. "open it" could be anything.
MINIMUM_WORDS_TO_ROUTE = 3


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
    source: str           # the model name, or "keywords"
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


# --- the model ---------------------------------------------------------------

# Built by concatenation rather than an escape, so the literal survives
# being written through a shell heredoc.
NEWLINE = chr(10)

# What each intent MEANS. Carried over from Jev's `criteria` mapping unchanged:
# every clause here was added because a real sentence went to the wrong place,
# and what changed in the swap is the classifier underneath them, not the
# distinctions themselves.
#
# These are descriptions rather than labels on purpose. The difference between
# "show" and "act" is the whole confirmation gate, and deserves a sentence.
INTENTS = {
    "answer": ("A question about facts, or about the user's own life, where "
               "being TOLD the answer is all they want. If they want the "
               "answer PUT somewhere - typed, written, saved - it is act or "
               "plan, not answer."),
    # "where is X" was landing on answer, because being TOLD where something
    # is genuinely is an answer. It has to be show, or the reply is a
    # description of where the button probably is instead of the cat going
    # to it.
    "show": ("Being SHOWN or TOLD, rather than having it done. Where a thing "
             "is on their screen; how to do something; a request to find, "
             "point at or highlight. 'Where is the X', 'how do I X', 'how can "
             "I X', 'show me how to X', 'can you take me to X'. If they want "
             "to LEARN it rather than have it happen, it is show."),
    # The boundary between these two is where the planner actually gets used,
    # and it was in the wrong place: "open notepad and type hello there"
    # scored as act, so the planner never ran for anything a person would
    # call a multi-step task.
    "act": ("ONE action they want CARRIED OUT. Press, click, type, open or "
            "close something. 'Open notepad', 'click save', 'do it'. Asking "
            "HOW to do a thing is show, not act - the difference is whether "
            "they want it explained or done. Read the conversation above: a "
            "sentence that continues what Meow just did is act, even when it "
            "names no application. After Meow opens Notepad, 'write something "
            "about X' means write it IN Notepad. "
            # "switch to my chrome window" scored show, which refuses every
            # tool that could bring a window forward - so the cat would have
            # pointed at the taskbar and changed nothing. Narrowly worded:
            # saying "go to notepad" here as well made the model read "take
            # me to my calendar settings" as act, which is the sentence the
            # show criteria name explicitly.
            "SWITCHING to another application or window is act, not show - "
            "'switch to my chrome window', 'bring chrome forward'. "
            "'Take me to X' is still show; that is asking to be shown where "
            "X is. "
            # "look up the best laptops under fifty thousand" scored show and
            # "do a research on gpu prices in india" scored plan. Both are one
            # job - find out and say it - and a plan would hand a one-step
            # job its own background window to narrate.
            "Looking something up on the web and telling them the answer is "
            "ONE action, so it is act: 'look up X', 'do a research on X', "
            "'find out X', 'google X'. It only becomes a plan if the findings "
            "have to be PUT somewhere afterwards."),
    "plan": ("THREE OR MORE actions, or anything that moves between "
             "applications - open something then do things in it, gather "
             "something then put it somewhere. If the sentence contains 'and' "
             "joining two different activities, it is a plan. ALSO: producing "
             "a spreadsheet, document or deck ABOUT a topic is always a plan, "
             "because the information has to be found before it can be "
             "written - 'gpu prices in india, put it in a spreadsheet' is a "
             "plan even though the word research was never said."),
}

ROUTER_PROMPT = (
    "You are the routing reflex of Meow, a voice companion that lives on the "
    "user's Windows desktop. It can talk, point at things on screen, press "
    "and type, and hand long jobs to a background agent."
    + NEWLINE + NEWLINE
    + "Classify what the user just said. Answer only with the fields asked "
      "for - you are not replying to the user, and nothing you write is read "
      "out."
    + NEWLINE + NEWLINE
    + "intent - what they want Meow to do:"
    + NEWLINE
    + NEWLINE.join(f"  {name}: {meaning}" for name, meaning in INTENTS.items())
    + NEWLINE + NEWLINE
    + "needs_screen - does answering require looking at what is currently on "
      "the user's screen? The three questions are INDEPENDENT; answer this "
      "one on its own merits. True when the sentence points at something only "
      "visible right now - 'this', 'that', 'here', 'what am i looking at', a "
      "board, a chart, an error message. False when the request names what it "
      "wants - 'open notepad', 'what is in my inbox', 'research gpu prices', "
      "'what is the capital of france'. Most sentences are false. A "
      "screenshot costs real money, so do not attach one just in case."
    + NEWLINE + NEWLINE
    + "risky - if this were carried out wrongly, would it change something "
      "the user would find HARD TO UNDO? True for deleting, sending, "
      "posting, buying, paying, overwriting, formatting, uninstalling - "
      "things that leave the machine or cannot be taken back. False for "
      "reading, looking, pointing, opening an application, typing text, "
      "saving a new file, or making a document or spreadsheet: those are "
      "ordinary and reversible. Being long or multi-step does not make "
      "something risky. Most sentences are false."
)

# A strict schema rather than a parsed sentence. The model cannot return an
# intent that is not one of the four, so there is no spelling to normalise and
# no prose to strip - the one failure mode left is the request itself failing,
# which falls back to keywords.
ROUTE_SCHEMA = {
    "title": "route",
    "description": "How to handle what the user just said.",
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "needs_screen": {"type": "boolean"},
        "risky": {"type": "boolean"},
    },
    "required": ["intent", "needs_screen", "risky"],
    "additionalProperties": False,
}


class ModelRouter:
    """A small chat model, asked three questions at once, through LangChain.

    ChatOpenAI rather than the OpenAI SDK so that routing traces alongside
    everything else - with Jev it was the one decision in a turn that LangSmith
    could not see, which made a slow turn hard to account for.
    """

    def __init__(self, api_key: str | None = None,
                 model: str = MODEL) -> None:
        from langchain_openai import ChatOpenAI

        key = api_key or get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set (model routing). "
                "Add it to .env, or Meow falls back to keyword routing."
            )

        self.model = model
        # temperature=0 because this is a classifier and the same sentence
        # should route the same way twice. Routing that drifts turn to turn is
        # indistinguishable from a bug in everything downstream of it.
        self._client = ChatOpenAI(
            model=model,
            api_key=key,
            temperature=0,
            max_completion_tokens=MAX_OUTPUT_TOKENS,
            timeout=TIMEOUT_SECONDS,
        ).with_structured_output(ROUTE_SCHEMA, method="json_schema")

    def route(self, text: str, partial: bool = False,
              context: str = "") -> Route:
        # The conversation goes in with the sentence. Without it every
        # sentence was classified alone, so "can you type about Elon Musk"
        # ten seconds after opening Notepad scored as a question ABOUT Elon
        # Musk - which is what it looks like, read by itself.
        said = (("Conversation so far:" + NEWLINE + context + NEWLINE
                 + "The user has just said: " + text)
                if context else "The user has just said: " + text)

        started = time.perf_counter()
        answer = self._client.invoke([
            ("system", ROUTER_PROMPT),
            ("human", said),
        ]) or {}
        elapsed = (time.perf_counter() - started) * 1000

        try:
            intent = Intent(str(answer.get("intent", "answer")))
        except ValueError:
            intent = Intent.ANSWER

        # The model's answer, ORed with the free keyword gate that has been
        # deciding this since before there was a router. They disagree in
        # opposite directions - the model missed "is there a typo in this
        # paragraph" and the keywords catch any demonstrative - and the costs
        # are not symmetric: a missed screenshot is a confident answer about
        # something the model cannot see, and an extra one is 2,833 tokens.
        # Nothing reads this outside the answer path, so a false positive on
        # "open notepad" costs nothing at all.
        needs_screen = (bool(answer.get("needs_screen", False))
                        or classify_screen(text) is not ScreenNeed.NONE)

        return Route(
            intent=intent,
            needs_screen=needs_screen,
            risky=bool(answer.get("risky", False)),
            source=self.model,
            milliseconds=elapsed,
            partial=partial,
        )


class Router:
    """Routes speech as it arrives, so the answer is ready when it is needed.

    Call `consider()` on every interim transcript. A transcript that has grown
    enough since the last one starts a routing call on a worker thread and the
    newest result wins; by the time the sentence ends, `latest` is usually
    already the right answer and `resolve()` returns immediately.

    Stale results are discarded by generation rather than by time. Interim
    transcripts arrive every few hundred milliseconds and routes can complete
    out of order, so "the most recent reply" and "the reply to the most recent
    text" are not the same thing.
    """

    def __init__(self, use_model: bool = True, memory=None) -> None:
        # The same Memory the harness and the answer path read. Routing was
        # the one decision still made with no idea what had happened before,
        # which is why a follow-up never resolved against the turn it
        # followed.
        self.memory = memory
        self.model: ModelRouter | None = None
        self.unavailable_reason: str | None = None

        if use_model:
            try:
                self.model = ModelRouter()
            except Exception as error:  # noqa: BLE001 - falls back, never fatal
                self.unavailable_reason = str(error).splitlines()[0]

        self.latest: Route | None = None
        self.latest_text = ""
        self._considered_words = 0
        self._generation = 0
        self._lock = threading.Lock()
        self.calls = 0
        self.wasted = 0   # routed a partial that the final made irrelevant
        self.skipped = 0  # a partial too close to the last one to be worth it

    @property
    def using_model(self) -> bool:
        return self.model is not None

    @property
    def source_name(self) -> str:
        """What is actually answering, for the startup banner."""
        return self.model.model if self.model is not None else "keywords"

    def _context(self) -> str:
        """Recent turns, or nothing when there is no memory to read."""
        return self.memory.recent() if self.memory is not None else ""

    def consider(self, partial_text: str) -> None:
        """Route an interim transcript in the background. Never blocks."""
        if not self.model:
            return

        words = len(partial_text.split())
        if words < MINIMUM_WORDS_TO_ROUTE:
            # Two words is not enough to classify, and routing every keystroke
            # of a sentence spends requests to answer the same question.
            return

        with self._lock:
            # A chat model charges per call, so a partial that has barely
            # changed is not worth one. Jev did not need this check: it scored
            # rather than wrote, so every interim was effectively free.
            if words < self._considered_words + MINIMUM_NEW_WORDS:
                self.skipped += 1
                return
            self._considered_words = words
            self._generation += 1
            generation = self._generation
        self.calls += 1

        # Read once, outside the worker. The memory is written from the
        # render loop, and a partial routed against context that changed
        # mid-flight is worse than one routed against slightly stale context.
        context = self._context()

        def run() -> None:
            try:
                route = self.model.route(partial_text, partial=True,
                                         context=context)
            except Exception:  # noqa: BLE001 - a failed guess is not an error
                return
            with self._lock:
                if generation != self._generation:
                    self.wasted += 1
                    return
                self.latest = route
                self.latest_text = partial_text

        threading.Thread(target=run, name="route", daemon=True).start()

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
                self._forget()
                return Route(route.intent, route.needs_screen, route.risky,
                             route.source, route.milliseconds, partial=False)
            time.sleep(0.01)

        self._forget()
        if self.model is not None:
            try:
                return self.model.route(final_text, context=self._context())
            except Exception:  # noqa: BLE001
                pass
        return classify_locally(final_text)

    def _forget(self) -> None:
        """Start the next sentence from nothing.

        The growth check is per sentence, not per session. Left alone, a long
        first utterance would raise the bar so high that a short second one
        never qualified, and every later turn would route from scratch on the
        critical path - the one place this whole file exists to avoid.
        """
        with self._lock:
            self._considered_words = 0
            self.latest = None
            self.latest_text = ""


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
