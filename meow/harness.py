"""The harness - Phase 1.5, on LangGraph.

One agent, a handful of tools, and the accessibility tree in context. Say
"click the close button" and it happens.

Built with `langchain.agents.create_agent` rather than raw tool calling, for
three reasons that are about the next phases rather than this one:

**Middleware is where safety belongs.** `HumanInTheLoopMiddleware` interrupts
before a risky tool runs, and the interrupt is part of the graph rather than a
callback somewhere in the caller. Phase 1.7 asked for exactly this.

**Checkpointing is the planner.** Phase 2 needs a plan that survives being
paused, and a graph with a checkpointer already does - the confirmation
interrupt and a resumable plan are the same mechanism.

**LangSmith is a requirement here, not a nice-to-have.** Every model call and
every tool result is traced without extra code, which is the only way to answer
"why did it press that?" once behaviour gets complicated.

**The model never produces a coordinate.** This is the payoff of Phase 1.1. The
digest lists controls by name, the agent asks for one BY NAME, and the name
resolves against the tree to an exact rectangle. The vision baseline emits
`[POINT:1265,12]` from a downscaled screenshot and is simply believed; it can be
thirty pixels out. A name cannot be thirty pixels out - it either exists or it
does not, and "it does not" is a visible, recoverable failure rather than a
silent click on the wrong thing.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Iterator

import warnings as _warnings


def _quiet_langgraph() -> None:
    """Stop a library notice printing above the prompt on every launch.

    LangGraph warns about a serializer default the first time its checkpoint
    module loads. Filtering it beforehand does not work, because LangChain
    registers an "always" filter for its own warning classes while IT loads,
    and that filter goes in front of ours.

    So the order matters: let LangChain load and register, then override with
    the specific class in hand, then touch the module that warns. Anything
    else either fires too early or gets overridden.

    The notice is addressed to whoever maintains this code, not to someone
    talking to a cat, and four lines of stack trace above the prompt teaches
    people to ignore the terminal - which is where the messages that matter go.
    """
    try:
        # It lives in _api.deprecation, not _api - importing the wrong
        # one returned early and the notice kept printing.
        from langchain_core._api.deprecation import (
            LangChainDeprecationWarning,
            LangChainPendingDeprecationWarning,
        )
    except Exception:  # noqa: BLE001 - cosmetic only, never fatal
        return
    for category in (LangChainPendingDeprecationWarning,
                     LangChainDeprecationWarning):
        _warnings.filterwarnings("ignore", category=category)
    try:
        import langgraph.checkpoint.serde.jsonplus  # noqa: F401
    except Exception:  # noqa: BLE001
        pass


_quiet_langgraph()

from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware, ModelCallLimitMiddleware,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from . import actions, apps
from .actions import Confirmer, Outcome, always_allow
from .config import get, openai_api_key
from .grounding import Target
from .risk import judge
from .uia import WindowDigest, digest_foreground

MODEL = "gpt-4o-mini"
MAX_OUTPUT_TOKENS = 220

# An agent that keeps deciding to click is the failure this project can least
# afford. A hard cap is cheaper than cleverness and cannot be talked out of.
MAX_MODEL_CALLS_PER_RUN = 8

SYSTEM_PROMPT = """You are a cat that lives on the user's Windows desktop. You \
can see the controls on their screen and you can operate them.

You can also open applications, switch between open windows, and press \
keyboard shortcuts. If what the user wants is not on screen, open it or switch \
to it rather than saying you cannot see it.

You are given a list of the controls currently on screen with their exact \
names. To act on one, call a tool with the control's name EXACTLY as it appears \
in that list. Never invent a name, and never guess coordinates - you do not \
need them, and the list is the truth about what exists.

If what the user asked for is not in the list, say so plainly and say what you \
can see instead. Never press something merely similar.

How you talk, and this matters as much as what you do:
- Write for the ear. This is read aloud. No markdown, no lists, no emoji.
- All lowercase.
- One or two sentences. Usually one.
- Never say "simply" or "just". Nothing is simple to someone who is stuck.
- Never end on a yes or no question.
- After acting, say what happened in a few words. Do not narrate beforehand."""

STYLE_REMINDER = (
    "Reply in lowercase, one or two short sentences, written to be read aloud. "
    "No markdown. Do NOT end on a question answerable with yes or no, "
    "including ones dressed as requests like 'can you tell me what you "
    "are trying to do?'. Ask for the thing itself, or say nothing."
)


@dataclass
class ToolRun:
    """One tool call and what came of it. Kept for the evaluation."""

    tool: str
    argument: str
    outcome: Outcome
    target: Target | None = None


@dataclass
class Confirmation:
    """The agent has paused and wants permission."""

    question: str


def enable_tracing() -> bool:
    """Turn on LangSmith if a key is present. Returns whether it is on."""
    key = get("LANGSMITH_API_KEY")
    if not key:
        return False
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = key
    os.environ.setdefault("LANGSMITH_PROJECT", get("LANGSMITH_PROJECT") or "meow")
    return True


class Harness:
    """An agent that can see the controls on screen and operate them."""

    def __init__(self, model: str = MODEL, confirm: Confirmer = always_allow,
                 ask_before_acting: bool = True) -> None:
        self.confirm = confirm
        # What the user actually said this turn. The risk policy needs it to
        # tell "open notepad" -> open Notepad, which is their own instruction,
        # from "open that" -> open Notepad, which is the cat's inference.
        self.transcript = ""
        self.route_risky = False
        self.digest: WindowDigest | None = None
        self.runs: list[ToolRun] = []
        self.last_error: str | None = None
        self.tracing = enable_tracing()

        # Tools close over `self` so they can reach the digest and record runs.
        # Defined here rather than at module level for that reason alone.

        @tool
        def click_control(name: str) -> str:
            """Press a control on screen. Use its exact name from the list."""
            target = self._resolve(name)
            if target is None:
                return self._no_such_control(name)
            outcome = actions.invoke(target, self._gated("click_control", name))
            self.runs.append(ToolRun("click_control", name, outcome, target))
            return outcome.detail

        @tool
        def point_at_control(name: str) -> str:
            """Move the pointer to a control to show where it is. Presses nothing."""
            target = self._resolve(name)
            if target is None:
                return self._no_such_control(name)
            outcome = actions.point_at(target)
            self.runs.append(ToolRun("point_at_control", name, outcome, target))
            return outcome.detail

        @tool
        def type_text(text: str) -> str:
            """Type text into whatever currently has keyboard focus."""
            outcome = actions.type_text(text, self._gated("type_text", text))
            self.runs.append(ToolRun("type_text", text, outcome))
            return outcome.detail

        @tool
        def open_app(name: str) -> str:
            """Open an application by name, such as chrome, word or notepad.

            Use when what the user wants is not on screen at all.
            """
            application = apps.find_application(name)
            if application is None:
                installed = [a.name for a in apps.list_applications()]
                near = [a for a in installed
                        if any(w in a for w in name.lower().split() if len(w) > 2)]
                suggestion = (f" Closest installed: {', '.join(near[:6])}."
                              if near else "")
                return f"No application called {name!r} is installed.{suggestion}"

            if not self._gated("open_app", application.name)(
                    f"open {application.name}?"):
                # Recorded, not just returned. The planner decides whether a
                # step succeeded by looking at these, and an early return with
                # no record made a refused step read as a completed one.
                refusal = Outcome(False, f"The user declined, so "
                                         f"{application.name} was not opened. "
                                         f"Nothing is wrong.", refused=True)
                self.runs.append(ToolRun("open_app", name, refusal))
                return refusal.detail

            started = apps.launch(application)
            outcome = Outcome(started,
                              f"opened {application.name}" if started
                              else f"could not open {application.name}",
                              method="launch")
            self.runs.append(ToolRun("open_app", name, outcome))
            if started:
                # An application takes a moment to put a window up, and the
                # control list is read from whatever is in front. Reading it
                # too early returns the OLD window and the next tool call acts
                # on the wrong application entirely.
                time.sleep(1.6)
                self.digest = digest_foreground()
            return outcome.detail

        @tool
        def switch_to_window(name: str) -> str:
            """Bring an already-open window to the front, by name or app."""
            window = apps.find_window(name)
            if window is None:
                open_now = [w.describe() for w in apps.list_windows()[:8]]
                return (f"No open window matches {name!r}. Open windows: "
                        f"{'; '.join(open_now)}")

            came_forward = apps.focus_window(window)
            outcome = Outcome(came_forward,
                              f"switched to {window.title[:50]}" if came_forward
                              else f"could not bring {window.title[:40]} forward",
                              method="focus")
            self.runs.append(ToolRun("switch_to_window", name, outcome))
            if came_forward:
                time.sleep(0.4)
                self.digest = digest_foreground()
            return outcome.detail

        @tool
        def list_open_windows() -> str:
            """What windows are open, to switch between."""
            windows = apps.list_windows()
            if not windows:
                return "No windows are open."
            return "Open windows: " + "; ".join(w.describe() for w in windows[:14])

        @tool
        def press_keys(keys: str) -> str:
            """Press a keyboard shortcut, such as "ctrl+t" or "enter".

            Use for things with no clickable control - opening a new tab,
            submitting a search, moving focus to an address bar.
            """
            outcome = actions.press_shortcut(keys, self._gated("press_keys", keys))
            self.runs.append(ToolRun("press_keys", keys, outcome))
            return outcome.detail

        @tool
        def list_controls() -> str:
            """Re-read the controls on screen, after something has changed."""
            self.digest = digest_foreground()
            if self.digest is None:
                return "No window is in the foreground."
            return self.digest.to_prompt()

        # The gate. Only the tools that change something are listed, so
        # pointing stays free - which is the autonomy decision this project was
        # built around, expressed as configuration rather than as a habit.
        # The risk policy inside each tool decides now, so the middleware gate
        # is off by default. Having both meant two prompts for one action, and
        # the middleware one could not see WHAT was about to be pressed - only
        # that something was.
        interrupts = {
            "click_control": True,
            "type_text": True,
            "open_app": True,
            "press_keys": True,
            # switch_to_window is NOT here. Bringing a window forward changes
            # nothing and the user can alt-tab straight back, so asking about
            # it is the kind of prompt that teaches people to stop reading
            # prompts.
        } if ask_before_acting else {}

        middleware = [ModelCallLimitMiddleware(
            run_limit=MAX_MODEL_CALLS_PER_RUN, exit_behavior="end")]
        if interrupts:
            middleware.insert(0, HumanInTheLoopMiddleware(
                interrupt_on=interrupts,
                description_prefix="Meow wants to",
            ))

        self.agent = create_agent(
            model=ChatOpenAI(model=model, api_key=openai_api_key(),
                             max_completion_tokens=MAX_OUTPUT_TOKENS),
            tools=[click_control, point_at_control, type_text, list_controls,
                   open_app, switch_to_window, list_open_windows, press_keys],
            system_prompt=SYSTEM_PROMPT,
            middleware=middleware,
            checkpointer=InMemorySaver(),
        )
        self._thread = 0

    # --- helpers --------------------------------------------------------

    def _resolve(self, name: str) -> Target | None:
        if self.digest is None:
            return None
        element = self.digest.find(name)
        return Target.from_element(element) if element else None

    def _no_such_control(self, name: str) -> str:
        """A miss that tells the agent what to try instead.

        A bare "no such control" is a dead end, and the agent answers a dead
        end by guessing again. One request for "terminal control" burned all
        six model calls that way without ever discovering that
        "Terminal (Ctrl+`)" exists.
        """
        near = self.digest.suggest(name) if self.digest else []
        if not near:
            return f"There is no control called {name!r} on screen."
        options = ", ".join(f'"{option}"' for option in near)
        return (f"There is no control called {name!r}. The closest on screen "
                f"are: {options}. Call the tool again with one of those exact "
                f"names, or say you cannot find it.")

    def _inner_confirm(self, question: str) -> bool:
        """Permission at the action layer, for callers with no policy."""
        return self.confirm(question)

    def _gated(self, tool: str, target: str):
        """A Confirmer that asks only when this particular action warrants it.

        Two rules, in order. Anything dangerous asks regardless of how plainly
        it was requested - "delete them" is a clear instruction and that is not
        a reason to skip the question. Anything the user named themselves does
        not ask, because repeating their sentence back and waiting is how a
        prompt becomes furniture.
        """
        decision = judge(tool, target, self.transcript, self.route_risky)

        def gate(question: str) -> bool:
            if not decision.should_ask:
                return True
            return self.confirm(question)

        return gate

    # --- running --------------------------------------------------------

    def answer(self, transcript: str) -> Iterator[str | Confirmation]:
        """Yield spoken sentences, and a Confirmation wherever it pauses.

        The caller answers a Confirmation by calling `allow()` or `deny()` and
        continuing to iterate. That shape exists because the voice loop has to
        ask out loud and wait, which a callback cannot express.
        """
        self.last_error = None
        self.runs.clear()
        self.transcript = transcript
        self.digest = digest_foreground()

        self._thread += 1
        config = {"configurable": {"thread_id": f"turn-{self._thread}"}}

        messages = [SystemMessage(self.digest.to_prompt())] if self.digest else []
        messages.append(HumanMessage(transcript))
        messages.append(SystemMessage(STYLE_REMINDER))

        try:
            yield from self._drain({"messages": messages}, config)
        except Exception as error:  # noqa: BLE001 - reported, never fatal
            self.last_error = f"{type(error).__name__}: {error}"

    def _drain(self, payload, config) -> Iterator[str | Confirmation]:
        """Run the graph, surfacing text and interrupts until it settles."""
        self._pending = None
        result = self.agent.invoke(payload, config=config)

        interrupts = result.get("__interrupt__") or []
        if interrupts:
            request = interrupts[0].value
            question = _interrupt_question(request)
            self._pending = config
            yield Confirmation(question)
            return

        for message in result.get("messages", []):
            if isinstance(message, AIMessage) and message.content:
                if "call limits exceeded" in str(message.content).lower():
                    # The cap did its job; the user should hear a sentence, not
                    # a middleware diagnostic. "Model call limits exceeded: run
                    # limit (6/6)" was read out loud, which is both alarming
                    # and meaningless to anyone who is not me.
                    yield ("i could not find that one, and i have stopped "
                           "looking rather than keep guessing.")
                    continue
                text = message.content
                if isinstance(text, list):  # content blocks
                    text = " ".join(
                        block.get("text", "") for block in text
                        if isinstance(block, dict))
                cleaned = text.strip()
                if cleaned:
                    yield cleaned

    def respond(self, allowed: bool) -> Iterator[str | Confirmation]:
        """Answer the pending Confirmation and carry on."""
        if self._pending is None:
            return
        config, self._pending = self._pending, None
        # A bare reject leaves the model to guess why the tool did not run, and
        # it guesses badly: asked to press a button and refused, it told the
        # user the button "seems to be disabled right now", which is alarming
        # and untrue. The message says what actually happened.
        decision = {"decisions": [
            {"type": "approve"} if allowed else {
                "type": "reject",
                "message": ("The user declined, so this was not done. "
                            "Nothing is wrong with the control."),
            }
        ]}
        yield from self._drain(Command(resume=decision), config)


def _interrupt_question(request) -> str:
    """Turn the interrupt payload into something a cat can say out loud.

    The middleware sends a structure, not a sentence: action_requests carrying
    a tool name and arguments, plus review_configs. The first version of this
    printed the raw dict at the user, which is exactly the kind of thing that
    makes software feel like it is talking to itself.
    """
    if isinstance(request, list) and request:
        request = request[0]
    if not isinstance(request, dict):
        return str(request)

    requests = request.get("action_requests")
    if isinstance(requests, list) and requests:
        action = requests[0]
        name = action.get("name", "")
        args = action.get("args") or {}
        subject = args.get("name") or args.get("text") or ""
        if name == "click_control":
            return f"press {subject}?"
        if name == "type_text":
            preview = subject if len(subject) <= 40 else subject[:40] + "..."
            return f'type "{preview}"?'
        return f"{name.replace('_', ' ')} {subject}?".strip()

    for key in ("description", "message", "question"):
        value = request.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(request)


def console_confirmer(question: str) -> bool:
    """Ask on the terminal. For testing; the voice loop asks out loud."""
    return input(f"  {question} [y/N] ").strip().lower() in ("y", "yes")
