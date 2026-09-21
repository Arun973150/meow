"""The harness - Phase 1.5.

One model, a handful of tools, and the accessibility tree in context. Say
"click the close button" and it happens.

**The model never guesses a coordinate.** This is the whole payoff of Phase 1.1.
The digest goes into the prompt as a list of named controls, so the model asks
for `click_control("Close")` and we resolve that name against the tree to an
exact rectangle. Compare with the vision baseline, where the model produces
`[POINT:1265,12]` from a downscaled screenshot and is simply believed. One of
those can be wrong by thirty pixels; the other cannot be wrong at all.

It also means the failure mode changes. Vision grounding fails by clicking the
wrong thing, silently. This fails by not finding the name, which is visible and
recoverable - the cat says it cannot see that control, which is true and useful.

**Tools stay few and general.** Invariant 1: capability grows through tools and
recipes, never through new agents. Four tools cover point, press, type and look,
and each declares what it costs if it was not what the user meant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Iterator

from openai import OpenAI

from . import actions
from .actions import Confirmer, Outcome, always_allow
from .config import openai_api_key
from .grounding import Target
from .mind import MAX_HISTORY_TURNS, SentenceChunker, Turn
from .uia import WindowDigest, digest_foreground

MODEL = "gpt-4o-mini"
MAX_OUTPUT_TOKENS = 220

SYSTEM_PROMPT = """You are a cat that lives on the user's Windows desktop. You \
can see the controls on their screen and you can operate them.

You are given a list of the controls currently on screen, with their exact \
names. To act on one, call a tool with the control's name EXACTLY as it appears \
in that list. Never invent a name, and never guess coordinates - you do not \
need them, and the list is the truth about what exists.

If what the user asked for is not in the list, say so plainly and say what you \
can see instead. Do not press something merely similar.

How you talk, and these matter more than what you say:
- Write for the ear. This is read aloud. No markdown, no lists, no emoji.
- All lowercase.
- One or two sentences. Usually one.
- Never say "simply" or "just". Nothing is simple to someone who is stuck.
- Never end on a yes or no question.
- After acting, say what happened in a few words. Do not narrate beforehand.

You are warm and brief."""


# Repeated immediately before the reply. The rules are in the system prompt too,
# but a hundred lines of control list between them and the request is enough to
# lose them.
STYLE_REMINDER = (
    "Reply in lowercase, one or two short sentences, written to be read aloud. "
    "No markdown. Do not end on a yes or no question."
)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "click_control",
            "description": (
                "Press a control on screen. Use the exact name from the list. "
                "This asks the user for permission first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact control name from the list",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "point_at_control",
            "description": (
                "Move the pointer to a control without pressing it, to show "
                "the user where it is. Safe, and never asks permission."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": (
                "Type text into whatever currently has keyboard focus. Asks "
                "permission first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_controls",
            "description": (
                "Re-read the controls on screen. Use after something has "
                "changed, such as a menu opening."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


@dataclass
class ToolRun:
    """One tool call and what came of it. Kept for the evaluation."""

    tool: str
    argument: str
    outcome: Outcome
    target: Target | None = None


class Harness:
    """A model that can see the controls on screen and operate them."""

    def __init__(self, model: str = MODEL, api_key: str | None = None,
                 confirm: Confirmer = always_allow) -> None:
        self._client = OpenAI(api_key=api_key or openai_api_key())
        self.model = model
        self.confirm = confirm
        self.history: list[Turn] = []
        self.digest: WindowDigest | None = None
        self.runs: list[ToolRun] = []
        self.last_error: str | None = None

    # --- tools ----------------------------------------------------------

    def _resolve(self, name: str) -> Target | None:
        if self.digest is None:
            return None
        element = self.digest.find(name)
        return Target.from_element(element) if element else None

    def _click_control(self, name: str) -> str:
        target = self._resolve(name)
        if target is None:
            # Visible, recoverable failure. Far better than pressing something
            # that merely looked similar.
            return f"There is no control called {name!r} on screen."
        outcome = actions.invoke(target, self.confirm)
        self.runs.append(ToolRun("click_control", name, outcome, target))
        return outcome.detail

    def _point_at_control(self, name: str) -> str:
        target = self._resolve(name)
        if target is None:
            return f"There is no control called {name!r} on screen."
        outcome = actions.point_at(target)
        self.runs.append(ToolRun("point_at_control", name, outcome, target))
        return outcome.detail

    def _type_text(self, text: str) -> str:
        outcome = actions.type_text(text, self.confirm)
        self.runs.append(ToolRun("type_text", text, outcome))
        return outcome.detail

    def _list_controls(self) -> str:
        self.digest = digest_foreground()
        if self.digest is None:
            return "No window is in the foreground."
        return self.digest.to_prompt()

    def _run_tool(self, name: str, arguments: dict) -> str:
        if name == "click_control":
            return self._click_control(arguments.get("name", ""))
        if name == "point_at_control":
            return self._point_at_control(arguments.get("name", ""))
        if name == "type_text":
            return self._type_text(arguments.get("text", ""))
        if name == "list_controls":
            return self._list_controls()
        return f"No tool called {name!r}."

    # --- the loop -------------------------------------------------------

    def answer(self, transcript: str, max_rounds: int = 4) -> Iterator[str]:
        """Yield spoken sentences, acting on the screen along the way.

        Bounded rounds rather than "until the model stops calling tools". An
        unbounded loop that decides to keep clicking is the failure mode this
        project can least afford, and a cap is a cheaper safeguard than
        cleverness.
        """
        self.last_error = None
        self.runs.clear()
        self.digest = digest_foreground()

        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for turn in self.history[-MAX_HISTORY_TURNS * 2:]:
            messages.append({"role": turn.role, "content": turn.text})
        if self.digest is not None:
            # System role, not user. As a user message the control list - often
            # a hundred lines - sat between the style rules and the request and
            # drowned them: the first version replied in capitalised paragraphs
            # ending on a yes/no question, which the prompt explicitly forbids.
            messages.append({"role": "system",
                             "content": self.digest.to_prompt()})
        messages.append({"role": "user", "content": transcript})
        # Restated last, where it is closest to the reply being written.
        messages.append({"role": "system", "content": STYLE_REMINDER})

        spoken: list[str] = []
        try:
            for _ in range(max_rounds):
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=TOOLS,
                    max_tokens=MAX_OUTPUT_TOKENS,
                )
                choice = response.choices[0].message

                if choice.content:
                    chunker = SentenceChunker()
                    for sentence in chunker.feed(choice.content):
                        spoken.append(sentence)
                        yield sentence
                    tail = chunker.flush()
                    if tail:
                        spoken.append(tail)
                        yield tail

                if not choice.tool_calls:
                    break

                messages.append(choice.model_dump(exclude_none=True))
                for call in choice.tool_calls:
                    try:
                        arguments = json.loads(call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    result = self._run_tool(call.function.name, arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result,
                    })

                # The screen has probably changed, so the old list is stale.
                # Re-reading costs ~270ms and stops the model pressing a menu
                # item that moved when the menu opened.
                self.digest = digest_foreground()

        except Exception as error:  # noqa: BLE001 - surfaced, never fatal
            self.last_error = f"{type(error).__name__}: {error}"
            return

        if spoken:
            self.history.append(Turn("user", transcript))
            self.history.append(Turn("assistant", " ".join(spoken)))


def console_confirmer(question: str) -> bool:
    """Ask on the terminal. For testing; the voice loop asks out loud."""
    answer = input(f"  {question} [y/N] ").strip().lower()
    return answer in ("y", "yes")
