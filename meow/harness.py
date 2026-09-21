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
from dataclasses import dataclass
from typing import Iterator

from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware, ModelCallLimitMiddleware,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from . import actions
from .actions import Confirmer, Outcome, always_allow
from .config import get, openai_api_key
from .grounding import Target
from .uia import WindowDigest, digest_foreground

MODEL = "gpt-4o-mini"
MAX_OUTPUT_TOKENS = 220

# An agent that keeps deciding to click is the failure this project can least
# afford. A hard cap is cheaper than cleverness and cannot be talked out of.
MAX_MODEL_CALLS_PER_RUN = 6

SYSTEM_PROMPT = """You are a cat that lives on the user's Windows desktop. You \
can see the controls on their screen and you can operate them.

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
    "No markdown. Do not end on a yes or no question."
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
                return f"There is no control called {name!r} on screen."
            outcome = actions.invoke(target, self._inner_confirm)
            self.runs.append(ToolRun("click_control", name, outcome, target))
            return outcome.detail

        @tool
        def point_at_control(name: str) -> str:
            """Move the pointer to a control to show where it is. Presses nothing."""
            target = self._resolve(name)
            if target is None:
                return f"There is no control called {name!r} on screen."
            outcome = actions.point_at(target)
            self.runs.append(ToolRun("point_at_control", name, outcome, target))
            return outcome.detail

        @tool
        def type_text(text: str) -> str:
            """Type text into whatever currently has keyboard focus."""
            outcome = actions.type_text(text, self._inner_confirm)
            self.runs.append(ToolRun("type_text", text, outcome))
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
        interrupts = {
            "click_control": True,
            "type_text": True,
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
            tools=[click_control, point_at_control, type_text, list_controls],
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

    def _inner_confirm(self, question: str) -> bool:
        """Permission at the action layer.

        The middleware has usually already asked by the time a tool runs, so
        this normally passes. It stays because `actions` must be safe to call
        from anywhere - the evaluation harness drives it directly, with no
        agent and no middleware in the way.
        """
        return self.confirm(question)

    # --- running --------------------------------------------------------

    def answer(self, transcript: str) -> Iterator[str | Confirmation]:
        """Yield spoken sentences, and a Confirmation wherever it pauses.

        The caller answers a Confirmation by calling `allow()` or `deny()` and
        continuing to iterate. That shape exists because the voice loop has to
        ask out loud and wait, which a callback cannot express.
        """
        self.last_error = None
        self.runs.clear()
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
