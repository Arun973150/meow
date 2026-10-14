"""One step at a time, decided against the screen in front of them.

`teach_me_this` writes the whole lesson in one call, which is right for a
keystroke drill - "press shift a, choose mesh, then uv sphere" is the same
instruction whatever is on screen, and knowing the lesson is twelve steps
long is how somebody hears "part 2 of 3" instead of a flat list.

It is wrong for anything click-driven, and the reason is not the writing.
A plan written before anything has happened describes a screen that does not
exist yet:

- a menu opens where the plan assumed a panel
- a dialog nobody predicted takes the focus
- the app is a version with the button somewhere else
- **they are two steps ahead**, because they knew that part

A written lesson absorbs none of those. It says step four while the screen
shows the result of step six, and the user is the one who has to work out
which of the two is wrong.

So this plans ONE step, from a fresh screenshot and what has happened so
far, and is asked again when that step completes. A wrong turn is not an
error to recover from - the next step simply comes from wherever they
actually are.

**IT IS NOT AN AGENT** (invariant 1). One model call, no tools, a strict
schema, and the app decides what to do with the answer - the same shape as
the editor in `study.py`. Nothing here loops on its own; the loop is the
user clicking.

**IT NEVER RETURNS A COORDINATE.** The teardown this came from has the model
emit `[TARGET:120,210,40:add modifier]`, pixels and radius included. This
project measured that approach at **0/30** and the dedicated computer-use
model at 52%, so a step names its target in WORDS and the app grounds it -
`locate_anything` first through the tree, which is exact, and only then by
sight. The acceptance radius then comes from how the thing was FOUND rather
than from what a model guessed about its size.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The same small model as the router and the query rewriter, and in the free
# 2.5M group. This runs once per step of a live lesson, so it is on the
# critical path between somebody clicking and hearing what to do next - the
# one place in this project where a cheap model is not a compromise but the
# requirement.
MODEL = "gpt-4o-mini"

# Enough for one sentence and one short target name. A step that needs more
# than this is several steps, which is the thing being prevented.
MAX_OUTPUT_TOKENS = 120

# A LESSON CANNOT RUN FOREVER, and the budget is what makes that true from
# the start rather than at the moment somebody gives up. Theirs is called
# "remaining click-to-advance turns"; the number here is ours, and it is
# generous because a real GUI job - export a video, set up a render - is
# genuinely twenty clicks, while a hundred means the lesson is lost and
# saying so is kinder than continuing.
MAX_ROUNDS = 25

# A step is one action, and the check is on LENGTH for the same reason it is
# in `teach_me_this`: a real single step reads "click Add Modifier" and no
# verb count can tell three clauses about one menu from three actions.
#
# TIGHTER than the written lesson's eighteen, deliberately. There a step is
# one step of a procedure - "press shift a, choose mesh, then uv sphere" is
# seven words and one menu. Here a step is ONE CLICK, and anything over a
# dozen words is several of them: "click add modifier, then choose
# subdivision surface, then set the levels to three" is sixteen and is three
# steps wearing one ring.
MANY_WORDS = 12

STEP_PROMPT = """You are teaching somebody to use the application in front of them, ONE STEP AT A TIME, by pointing at what to click next.

You are shown their screen as it is RIGHT NOW, and what has already happened. Give exactly ONE step: the next single thing they do.

THE SCREEN IS THE ONLY TRUTH. Not the plan you would have written, not what you said last time. If what you see is not what you expected:
- a menu or panel you aimed at is now OPEN - that step worked, do not repeat it, go on to what is inside it
- something you did not expect is on screen - deal with THAT first, it is where they are
- they are further along than you last said - they knew that part and did it, carry on from here
- nothing you can use is on screen - set lost to true rather than guessing

ONE IMMEDIATE ACTION. "Click Add Modifier" is a step. "Click Add Modifier, then choose Subdivision, then set levels to 3" is three, and saying it in one breath is the recitation this exists to replace.

`target` NAMES what to click, as the words that appear on their screen: "Add Modifier", "the wrench tab", "File". Never coordinates, never a description of where it is on screen - the app finds it and circles it. Keep it to a few words, and make them words that are ON the screen.

`kind`:
- "click" when the step is clicking or hovering something that is visible. This is the normal one.
- "do" when there is nothing to click - a keystroke ("press tab"), a drag, a slider, picking a colour, typing a value. Leave `target` empty unless there is something worth circling while they do it. They will say when they have done it.

`instruction` is EXACTLY what gets said out loud to them. One short sentence, lower case, no preamble, no "now we will", no listing what comes after. Just the action.

`done` is true when what they asked for is FINISHED and on screen - then `instruction` says so in one line and nothing is circled.

Do not say how many steps are left. You do not know; you are deciding one at a time."""

STEP_SCHEMA = {
    "title": "step",
    "description": "The single next thing the user does.",
    "type": "object",
    "properties": {
        "instruction": {"type": "string"},
        "target": {"type": "string"},
        "kind": {"type": "string", "enum": ["click", "do"]},
        "done": {"type": "boolean"},
        "lost": {"type": "boolean"},
    },
    "required": ["instruction", "target", "kind", "done", "lost"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Step:
    """One step, as the model decided it against the current screen."""

    instruction: str
    target: str = ""
    kind: str = "click"
    done: bool = False
    lost: bool = False

    @property
    def wants_a_click(self) -> bool:
        """Is this one the app watches the mouse for?

        A click step with no target is not watchable - there is nothing to
        circle and nothing to compare a click against - so it degrades to a
        spoken step rather than waiting for something that can never arrive.
        """
        return self.kind == "click" and bool(self.target.strip())

    @property
    def crammed(self) -> bool:
        return len(self.instruction.split()) > MANY_WORDS


@dataclass
class Done:
    """What happened to a step, for the next round to read."""

    instruction: str
    how: str          # "clicked", "told me", "skipped ahead", "could not find it"

    def describe(self) -> str:
        return f"{self.instruction}  ({self.how})"


@dataclass
class StepByStep:
    """A live lesson that decides each step when it is needed.

    Holds the goal and the history, and nothing about the screen: a round is
    handed the evidence by the caller, because the app owns the capture and
    the digest and this must not grow its own.
    """

    goal: str
    walked: list[Done] = field(default_factory=list)
    rounds: int = 0
    last_error: str = ""

    @property
    def spent(self) -> bool:
        return self.rounds >= MAX_ROUNDS

    def record(self, instruction: str, how: str) -> None:
        self.walked.append(Done(instruction, how))

    def so_far(self) -> str:
        """What has happened, for the prompt.

        Each entry says HOW it completed, not only that it did. "clicked" and
        "told me" are different evidence - one was observed and the other was
        volunteered - and a step recorded as "could not find it" is the one
        thing that must not be repeated identically next round.
        """
        if not self.walked:
            return "Nothing yet. This is the first step."
        lines = ["What has happened so far, oldest first:"]
        lines.extend(f"  {number}. {done.describe()}"
                     for number, done in enumerate(self.walked, start=1))
        return chr(10).join(lines)

    def where_we_are(self) -> str:
        """The lesson, for the HARNESS prompt, when a question interrupts it.

        Deliberately says the count of steps WALKED and nothing about what
        comes next, because nothing knows what comes next - that is the whole
        design. The written-lesson version of this handed the model every
        remaining step and it read one out loud; here there is nothing to
        leak.
        """
        lines = [f"YOU ARE TEACHING THEM, STEP BY STEP: {self.goal}"]
        if self.walked:
            lines.append(f"They have done {len(self.walked)} step(s) already, "
                         f"the last being: {self.walked[-1].instruction}")
        lines.append(
            "The next step is decided from their screen when they finish "
            "this one, so you do NOT know what it is - do not guess one, do "
            "not re-plan the lesson, and do not ask what they want to do "
            "next. Answer what they asked, about where they are.")
        return chr(10).join(lines)


class Teacher:
    """Asks for one step. Built once per lesson and kept."""

    def __init__(self, api_key: str | None = None, model: str = MODEL) -> None:
        from langchain_openai import ChatOpenAI

        from ..config import get

        key = api_key or get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set (step by step).")
        self._client = ChatOpenAI(
            model=model,
            api_key=key,
            temperature=0,
            max_completion_tokens=MAX_OUTPUT_TOKENS,
        ).with_structured_output(STEP_SCHEMA, method="json_schema")

    def next_step(self, lesson: StepByStep, screen) -> Step | None:
        """The next step, or None if it could not be decided.

        `screen` is whatever the caller has: a `HumanMessage` carrying the
        picture, the digest, the OCR of a blind window. Built by the app,
        because it already pays for all three and knows which are worth
        sending.

        None rather than a guess. A lesson that cannot see is better ended
        than continued from an invention, and the caller says so out loud.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        lesson.rounds += 1
        try:
            answer = self._client.invoke([
                SystemMessage(STEP_PROMPT),
                SystemMessage(f"THE GOAL: {lesson.goal}"),
                SystemMessage(lesson.so_far()),
                screen if not isinstance(screen, str) else HumanMessage(screen),
            ])
        except Exception as error:  # noqa: BLE001 - end the lesson, never crash
            lesson.last_error = f"{type(error).__name__}: {error}"
            return None
        if not isinstance(answer, dict):
            lesson.last_error = f"unexpected reply: {type(answer).__name__}"
            return None
        # THE SCHEMA ENVELOPE, WHICH IT SOMETIMES ECHOES. Seen live, four
        # rounds running: {"type": "object", "properties": {"instruction":
        # "click File", ...}} - every field read empty, nothing was said, and
        # each round still cost a model call and a screenshot. Nothing in the
        # reply looked like a failure.
        if "instruction" not in answer and isinstance(
                answer.get("properties"), dict):
            answer = answer["properties"]
        return Step(
            instruction=str(answer.get("instruction") or "").strip(),
            target=str(answer.get("target") or "").strip(),
            kind=str(answer.get("kind") or "click"),
            done=bool(answer.get("done")),
            lost=bool(answer.get("lost")),
        )
