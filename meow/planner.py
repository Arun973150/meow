"""Multi-step tasks - Phase 2.1, as a LangGraph state machine.

"open chrome, search for solar panel costs, and put the first three results in
a spreadsheet" is four or five actions with a shape. The harness can do each of
them and cannot hold the whole thing, because a single conversation that grows
by a screenful of controls per step runs out of room and out of attention.

**The plan IS the state.** Invariant 4 says the plan must be state rather than
context, and on a graph that stops being a convention someone has to remember:
`PlanState` is the state, LangGraph carries it between nodes, and the
checkpointer persists it. Nothing has to be re-read into a prompt to be
remembered.

Three things follow, and they are the point rather than side effects:

**It can be shown.** A plan that exists as state can be read out - "step three
of five, opening the spreadsheet" - where a plan buried in a conversation can
only be guessed at.

**It can be stopped and resumed.** Each step is a node, so a checkpoint lands
between steps rather than halfway through pressing something. The panic key
lands on a boundary, and what was already done stays done - the graph can be
invoked again on the same thread id and carries on where it left off.

**It can be repaired.** A failed step is a state transition, visible in the
state and in LangSmith. The alternative - a model quietly deciding halfway
through that it will do something else - is the failure this shape prevents.

Each step reaches the harness on its own, with the controls for the screen as
it is *then* and no transcript of what came before. That is what keeps a
six-step task costing the same per step as a two-step one, and it is why the
steps have to be written as standalone instructions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Callable, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .config import openai_api_key

MODEL = "gpt-4o-mini"

# A spoken request needing more than this is not a plan, it is a project, and
# the honest answer is to say so. It also caps what a runaway plan can do.
MAX_STEPS = 8

PLANNER_PROMPT = """You break a spoken request into the fewest steps that \
actually do it, for a cat that operates a Windows desktop.

It can: open applications, switch between open windows, click controls by name, \
type text, and press keyboard shortcuts.

Rules:
- Each step is ONE action, phrased as an instruction: "open chrome", \
"press ctrl+t", "type solar panel costs", "press enter".
- Do not include steps for looking, checking or waiting. It looks at the \
screen before every step anyway.
- Do not plan past what was asked.
- Between two and six steps. If it needs more than that, return one step \
saying it is too big.

Reply with JSON only: {"steps": ["...", "..."]}"""


class StepState(Enum):
    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Step:
    instruction: str
    state: StepState = StepState.WAITING
    said: str = ""

    def describe(self) -> str:
        marks = {StepState.WAITING: " ", StepState.RUNNING: ">",
                 StepState.DONE: "x", StepState.FAILED: "!",
                 StepState.SKIPPED: "-"}
        return f"[{marks[self.state]}] {self.instruction}"


def _keep_last(_old: Any, new: Any) -> Any:
    """Reducer: the newest value wins.

    Spelled out rather than left to the default, because the default for an
    un-annotated key is also last-write-wins and that is worth stating where
    the state is the whole design.
    """
    return new


class PlanState(TypedDict, total=False):
    """Everything the plan is. Persisted by the checkpointer between steps."""

    goal: Annotated[str, _keep_last]
    steps: Annotated[list[Step], _keep_last]
    index: Annotated[int, _keep_last]
    abandoned: Annotated[bool, _keep_last]
    stopped: Annotated[bool, _keep_last]


@dataclass
class Plan:
    """A readable view over the graph's state.

    The graph works on a TypedDict because that is what LangGraph persists;
    everything else in Meow wants an object with properties. This is the seam
    between the two rather than a second source of truth.
    """

    goal: str
    steps: list[Step] = field(default_factory=list)
    abandoned: bool = False
    stopped: bool = False

    @classmethod
    def from_state(cls, state: PlanState) -> "Plan":
        return cls(
            goal=state.get("goal", ""),
            steps=list(state.get("steps") or []),
            abandoned=bool(state.get("abandoned")),
            stopped=bool(state.get("stopped")),
        )

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def finished(self) -> int:
        return sum(1 for step in self.steps
                   if step.state in (StepState.DONE, StepState.SKIPPED))

    @property
    def complete(self) -> bool:
        return bool(self.steps) and all(
            step.state is not StepState.WAITING for step in self.steps)

    @property
    def succeeded(self) -> bool:
        return bool(self.steps) and all(
            step.state is StepState.DONE for step in self.steps)

    def progress(self) -> str:
        """What the cat says between steps. Short, because it is spoken."""
        return f"step {min(self.finished + 1, self.total)} of {self.total}"

    def summary(self) -> str:
        return "\n".join(step.describe() for step in self.steps)


def parse_steps(text: str) -> list[Step]:
    """Pull steps out of a model reply, tolerating a stray code fence."""
    text = text.strip()
    if text.startswith("```"):
        # Asked for JSON, sometimes fenced anyway. Cheaper to cut the fence
        # than to keep arguing with the prompt.
        text = text.split("```")[1].removeprefix("json").strip()
    try:
        raw = json.loads(text).get("steps", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    return [Step(str(item).strip()) for item in raw if str(item).strip()][:MAX_STEPS]


# Asking to be told what is happening, rather than just to have it happen.
# Without one of these the plan runs quietly and says one line at the end.
EXPLAIN_WORDS = (
    "explain", "tell me", "walk me through", "narrate", "talk me through",
    "step by step", "show me how", "what are you doing", "describe",
    "teach me", "how do i", "how do you",
)


def wants_narration(goal: str) -> bool:
    """Did they ask to be told, or just to have it done?

    Narrating every step is right when someone is learning and wrong when they
    are busy. A two-step task narrated in full is five spoken lines, four of
    which say what the fifth already implies, and the machine sat idle through
    all of them.
    """
    lowered = goal.lower()
    return any(phrase in lowered for phrase in EXPLAIN_WORDS)


class Planner:
    """A graph that plans, then runs each step as its own node visit."""

    def __init__(self, harness, model: str = MODEL,
                 on_event: Callable[[str, str], None] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> None:
        self.harness = harness
        self.on_event = on_event
        self.should_stop = should_stop
        # Set per run. Quiet by default: the thinking dots already say that
        # something is happening, and saying it out loud as well delays it.
        self.narrate = False
        self._client = ChatOpenAI(model=model, api_key=openai_api_key(),
                                  max_completion_tokens=400)

        graph = StateGraph(PlanState)
        graph.add_node("plan", self._plan)
        graph.add_node("step", self._step)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges("plan", self._after_plan,
                                    {"step": "step", "end": END})
        # The loop. A conditional edge back to the same node is what makes each
        # step its own checkpoint, which is what makes the plan resumable.
        graph.add_conditional_edges("step", self._after_step,
                                    {"step": "step", "end": END})

        self.graph = graph.compile(checkpointer=InMemorySaver())
        self._runs = 0

    def _report(self, kind: str, text: str) -> None:
        if self.on_event is not None:
            self.on_event(kind, text)

    # --- nodes -----------------------------------------------------------

    def _plan(self, state: PlanState) -> PlanState:
        reply = self._client.invoke([
            SystemMessage(PLANNER_PROMPT),
            HumanMessage(state["goal"]),
        ])
        steps = parse_steps(str(reply.content))
        # Deliberately silent. An earlier version said "okay, 4 steps" here,
        # which cost a whole spoken sentence before anything happened and said
        # nothing the first step does not - "step 1 of 4" carries the same
        # count. The thinking dots cover the pause while this runs, and they
        # cover it without delaying the work.
        return {"steps": steps, "index": 0, "abandoned": not steps}

    def _step(self, state: PlanState) -> PlanState:
        from .harness import Confirmation

        steps = list(state["steps"])
        index = state.get("index", 0)
        step = steps[index]

        if self.should_stop is not None and self.should_stop():
            for later in steps[index:]:
                later.state = StepState.SKIPPED
            self._report("stopped", "stopped partway through.")
            return {"steps": steps, "index": len(steps), "stopped": True}

        step.state = StepState.RUNNING
        plan = Plan.from_state({**state, "steps": steps})
        if self.narrate:
            self._report("step", f"{plan.progress()}, {step.instruction}")
        else:
            # Printed, not spoken. The terminal is for watching; the voice is
            # for the answer.
            self._report("quiet", f"{plan.progress()}, {step.instruction}")

        said: list[str] = []
        try:
            # Cleared per step: a refusal from an earlier one must not make
            # this one look refused too.
            self.harness.runs.clear()
            stream = self.harness.answer(step.instruction)
            while True:
                try:
                    event = next(stream)
                except StopIteration:
                    break
                if isinstance(event, Confirmation):
                    allowed = self.harness.confirm(event.question)
                    stream = self.harness.respond(allowed)
                    continue
                said.append(str(event))
                self._report("say" if self.narrate else "quiet", str(event))

            step.said = " ".join(said)

            # A refusal is not an error, so last_error stays clear and the step
            # looks successful. The tool record is the only place it shows -
            # without this check, a plan the user declined reported as done.
            refused = any(run.outcome.refused for run in self.harness.runs)
            failed = bool(self.harness.last_error) or refused
            step.state = StepState.FAILED if failed else StepState.DONE

            if failed:
                if refused and not self.harness.last_error:
                    self._report("say", "you said no, so i have stopped here.")
                else:
                    self._report("error",
                                 self.harness.last_error or "that step failed")
                for later in steps[index + 1:]:
                    later.state = StepState.SKIPPED
                return {"steps": steps, "index": len(steps)}

        except Exception as error:  # noqa: BLE001 - a bad step is not a crash
            step.state = StepState.FAILED
            step.said = f"{type(error).__name__}: {error}"
            self._report("error", step.said)
            for later in steps[index + 1:]:
                later.state = StepState.SKIPPED
            return {"steps": steps, "index": len(steps)}

        return {"steps": steps, "index": index + 1}

    # --- edges -----------------------------------------------------------

    @staticmethod
    def _after_plan(state: PlanState) -> str:
        return "end" if state.get("abandoned") else "step"

    @staticmethod
    def _after_step(state: PlanState) -> str:
        return "step" if state.get("index", 0) < len(state["steps"]) else "end"

    # --- running ---------------------------------------------------------

    def run(self, goal: str, thread: str | None = None,
            narrate: bool | None = None) -> Plan:
        """Plan the goal and carry it out. Returns the finished plan.

        `narrate` defaults to whether the request asked to be told. Someone
        learning wants every step; someone busy wants the thing done.
        """
        self.narrate = wants_narration(goal) if narrate is None else narrate
        self._runs += 1
        config = {"configurable": {"thread_id": thread or f"plan-{self._runs}"},
                  # Every step is a node visit, so the default of 25 would cap
                  # a plan at far fewer steps than MAX_STEPS allows.
                  "recursion_limit": MAX_STEPS * 3 + 10}
        final = self.graph.invoke({"goal": goal, "index": 0}, config=config)
        return Plan.from_state(final)


def make_plan(goal: str, model: str = MODEL) -> Plan:
    """Just the planning half, without running anything. Used by tests."""
    client = ChatOpenAI(model=model, api_key=openai_api_key(),
                        max_completion_tokens=400)
    reply = client.invoke([SystemMessage(PLANNER_PROMPT), HumanMessage(goal)])
    steps = parse_steps(str(reply.content))
    return Plan(goal=goal, steps=steps, abandoned=not steps)
