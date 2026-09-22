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
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Callable, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from ..config import openai_api_key

MODEL = "gpt-4o-mini"

# A spoken request needing more than this is not a plan, it is a project, and
# the honest answer is to say so. It also caps what a runaway plan can do.
MAX_STEPS = 8

PLANNER_PROMPT = """You break a spoken request into the fewest steps that actually do it, for a cat that operates a Windows desktop.

It can:
- open applications, switch between open windows, minimise and maximise them
- click controls by name, type exact text, press keyboard shortcuts
- LOOK UP a topic on the web and read the top pages
- WRITE text about a topic, in its own words
- MAKE a Word document, a spreadsheet or a slide deck, saved to Documents/Meow
- OPEN the document it just made
- FIND HOW a setting works when it is not on screen, then point at it

Rules:
- Each step says WHAT to achieve, not which keys to hit. "minimise vs code" is a step; "press alt+space" then "click minimize" is you guessing at how, and guessing wrong. It works out how.
- Say "write about X" when the user wants something composed, and "type X" only when they gave you the exact words. "Write about Elon Musk" means write several sentences about him, NOT type his name.
- Do not add steps nobody asked for. No pressing enter at the end, no saving, no closing, no tidying up.
- Do not include steps for looking, checking or waiting. It looks at the screen before every step anyway.
- Two to six steps. If it genuinely needs more, return one step saying it is too big - but count properly first: looking something up and writing it to a file is TWO steps, not ten.
- If the request is vague, DO NOT ask what they meant. Plan the most useful reading of it. You are running in the background and nobody is there to answer.

Reply with JSON only: {"steps": ["...", "..."]}

Examples:
  "minimise vs code then open notepad and write about llms"
  -> {"steps": ["minimise visual studio code", "open notepad", "write about large language models"]}

  "open chrome and search for solar panel costs"
  -> {"steps": ["open chrome", "press ctrl+t", "type solar panel costs", "press enter"]}

  "find research on solar panel costs and put it in a spreadsheet"
  -> {"steps": ["look up solar panel costs", "make a spreadsheet of what you found"]}

  "write me a report about llm training and open it"
  -> {"steps": ["look up llm training costs", "make a document about llm training", "open the document"]}

RESEARCH AND DOCUMENTS ARE ONE STEP EACH. "look up X" is a single step - do not break it into opening a browser, typing and reading. "make a spreadsheet" is a single step - it writes the file directly and does not need Excel opened first."""


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


def checkpoint_path():
    """Where plan state is written. Beside the conversations, not in temp.

    A checkpoint in a temp folder is a checkpoint that is gone when it is
    wanted - which is after a crash, a reboot, or a machine that went to sleep
    in the middle of a long job.
    """
    from ..storage import plans_database

    return plans_database()


def make_checkpointer():
    """Durable if possible, in-memory if not. Never fails.

    Every step of a plan is a checkpoint, which is what makes a plan
    resumable - but only within one process while the saver is in memory. A
    plan interrupted by a crash was simply gone, along with any record of what
    it had already done, which is the moment the state is worth the most.

    `check_same_thread=False` because plans run on task threads while the
    voice loop holds the same saver. Falling back rather than raising: a plan
    that keeps its state only in memory still works, and a cat that will not
    start because a database file is locked does not.
    """
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        connection = sqlite3.connect(str(checkpoint_path()),
                                     check_same_thread=False)
        return SqliteSaver(connection)
    except Exception:  # noqa: BLE001
        return InMemorySaver()


class Planner:
    """A graph that plans, then runs each step as its own node visit."""

    def __init__(self, harness, model: str = MODEL,
                 on_event: Callable[[str, str], None] | None = None,
                 unattended: bool = False,
                 should_stop: Callable[[], bool] | None = None) -> None:
        self.harness = harness
        self.on_event = on_event
        # True when this is a handed-over task. Its confirmer declines without
        # asking anyone, so a refusal here is NOT the user having said no -
        # and saying they did is a plain untruth about something they never
        # saw.
        self.unattended = unattended
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

        self.graph = graph.compile(checkpointer=make_checkpointer())
        self._runs = 0
        # The id of the last plan run, so a caller can say which thread
        # to look at afterwards. Without it the saved state exists and
        # nothing knows what to call it.
        self.thread = ""

    def saved_state(self, thread: str | None = None):
        """What a plan had done, read back from the checkpoint.

        The point of persisting any of this: after a crash, "what had it
        already finished" is answerable instead of guessed at.
        """
        try:
            snapshot = self.graph.get_state(
                {"configurable": {"thread_id": thread or self.thread}})
            return Plan.from_state(snapshot.values) if snapshot.values else None
        except Exception:  # noqa: BLE001
            return None

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
            # Checked and found NOT to have happened. A could-not-tell is
            # deliberately not in here: clicking into a text box changes
            # nothing observable, and a plan that stopped on every unverifiable
            # step would stop constantly. A definite no is different, and
            # continuing past one is how four steps ran against a Chrome
            # profile picker - no tab opened, nothing typed, Enter pressed at
            # nothing - and then reported on what they had done.
            denied = self.harness.denied_by_the_verifier()
            failed = bool(self.harness.last_error) or refused or bool(denied)
            step.state = StepState.FAILED if failed else StepState.DONE

            if failed:
                if refused and not self.harness.last_error:
                    self._report("say",
                                 "that needed your permission and you were "
                                 "not here, so i left it."
                                 if self.unattended
                                 else "you said no, so i have stopped here.")
                elif denied and not self.harness.last_error:
                    # Say what was checked, not just that something went
                    # wrong. "The new tab did not open" is something the user
                    # can look at and act on; "that step failed" is not.
                    self._report("say",
                                 f"{denied}, so i stopped rather than "
                                 f"carrying on as if it had.")
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
        # Unique per plan, and durable. Reusing an id would resume the
        # previous plan's state into this one, which is worse than having none.
        self.thread = thread or f"plan-{int(time.time())}-{self._runs}"
        config = {"configurable": {"thread_id": self.thread},
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
