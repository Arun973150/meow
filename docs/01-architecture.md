# Architecture

## Handing work over - built

A plan that takes half a minute runs as a **background task** rather than in the
voice loop. The cat says it has started and returns to listening; the task gets
its own thread and a small window that reports as it goes.

| | |
|---|---|
| spawn | any `plan` intent, up to two at once |
| add to one | say "also ...", "add ...", "as well" — queued behind the current step |
| finish | it says so and stays on screen until dismissed |
| dismiss | "close that" — clears finished windows, leaves running ones |
| stop | the panic key reaches tasks; they check between steps |

**Queued, not applied immediately.** Interrupting a half-written spreadsheet to
add a column produces neither the spreadsheet nor the column.

**Trigger words are explicit.** Guessing whether a sentence belongs to a running
task is wrong in both directions, and being wrong means either a lost
instruction or a hijacked one.

**A finished task waits.** One that vanishes leaves the user unsure whether it
worked, and the moment it finishes is when a follow-up is easiest to ask.

Clicky's commercial build has something similar — *"drop the agent magic word
and it spawns a background agent"* — but no implementation is public and the
open build has no agents at all. This is built from the requirement.



One harness, two specialists, two modes. Capability grows through tools and
recipes — never through new agents.

```
                 ROUTER   reflex: route · needs_screen · risky  (nano)
                     │
       ┌─────────────┼──────────────┐
       │  fast path  │              │  fast path
       ▼             ▼              ▼
  ┌─────────┐   ┌─────────────┐   ┌───────────┐
  │RESEARCH │◀──│   HARNESS   │──▶│ EXPLAINER │
  │         │──▶│             │◀──│           │
  │ search  │   │  ~19 tools  │   │ plan→code │
  │ fetch   │   │  2 prompts  │   │ →lint→    │
  │         │   │             │   │  render→  │
  │ no files│   │  REACTIVE   │   │  verify   │
  │ no desk │   │     or      │   │           │
  │ no send │   │ DELIBERATE  │   │ manim RAG │
  └─────────┘   │      ↓      │   └───────────┘
                │   PLANNER   │
                │ plan=state  │
                └─────────────┘
```

---

## The router — the reflex layer

One small model answering three questions about the sentence that just arrived:
what the user wants, whether it needs the screen, whether getting it wrong
would change something hard to undo. `meow/agent/router.py`.

**This used to be Jev** — TypeSafe's "System One" evaluation model, which
scored typed questions and could not generate text at all. It was the right
shape for the job and it is gone: no key, no service. The replacement is
`gpt-4.1-nano` with a strict JSON schema, which is the same small model the
query rewriter uses and the cheapest thing in the project.

**The criteria survived the swap unchanged.** Every clause describing what
`answer`, `show`, `act` and `plan` mean was written because a real spoken
sentence went somewhere useless, and none of that is about the classifier
reading them. They were lifted from Jev's `criteria` mapping word for word.

```python
ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string",
                   "enum": ["answer", "show", "act", "plan"]},
        "needs_screen": {"type": "boolean"},
        "risky": {"type": "boolean"},
    },
    "required": ["intent", "needs_screen", "risky"],
    "additionalProperties": False,
}
```

| Output | Drives |
|---|---|
| `intent` | which path runs |
| `needs_screen` | whether the answer path pays for a screenshot |
| `risky` | one more reason for the confirmation gate to ask ([03-safety.md](03-safety.md)) |

A strict schema rather than parsed prose: the model cannot return an intent
that is not one of the four, so there is no spelling to normalise and no
sentence to strip. The one failure left is the request itself failing, which
falls back to keywords.

**The router is an API call, not local.** The panic-abort path must never
depend on it — that stays local keyword matching.

### What got worse, and what was done about it

**Three things, and none of them is accuracy.** `meow routing` scores the
swap at **36/37 on intent, 6/6 on `needs_screen`, 36/36 on `risky`**, against
18/19 measured for Jev on a smaller set.

**It is slower.** 1,214ms median against Jev's ~560ms warm. That is survivable
only because routing runs off the critical path, which makes overlapping it
with the UIA digest more important than it was, not less.

**It costs per call.** Jev charged for thinking rather than tokens, so routing
every interim transcript was free. A chat model is not, and a sentence emits
five or six interims — so a partial is only routed once it has grown by three
words since the last one. Without that, routing alone would cost about as much
as the rest of the turn.

**`temperature=0` is not determinism.** Two sentences in the measured set
change route between runs: "open notepad then type hi my name is srija" about
one run in five, "summarise what i am reading" about two in five. Jev returned
calibrated probabilities and held still. `meow routing --repeat 3` is there to
tell a rule worth writing from a model that will not sit down.

**And one thing is strictly better:** every path in a turn is `ChatOpenAI` now,
so LangSmith traces the routing decision alongside everything else. Jev was the
one step in a turn that tracing could not see.

### The latency trick

Routing runs on interim transcripts while the user is still speaking, so by the
time they stop talking the decision is usually already made.

```
serial   STT 250ms → route 1200ms → capture 200ms → LLM 450ms → TTS 120ms
parallel STT 250ms → [route and capture already done] → LLM 450 → TTS 120
```

With Jev this was free. With a chat model it is paid for, and the debounce
above is what keeps the bill honest.

### Corrections, which are not the model's job

Some routing facts are not classification problems — they are facts about what
the cat can do, which no prompt teaches a model that cannot see the tool list.
Those live in `meow/language/routing.py` as pure functions over a route, and
each one exists because a real sentence went somewhere useless. The cost of
being wrong is asymmetric, so they are decided in code rather than weighed.

Measured: four corrections fire across the 37-sentence set, and the model
alone scores 28/37 without them.

---

## The harness

`create_agent` from `langchain` (note: `create_react_agent` is deprecated). Its
**middleware system** is where the risk gate plugs in, which is why the agent
loop, the safety gate, and LangSmith tracing all share one mechanism.

### Two prompts, not two agents

| Prompt | Used for |
|---|---|
| **voice** | conversational replies. Write for the ear — lowercase, no markdown, no lists, short sentences |
| **work** | tool-calling. Structured, terse, no personality |

These genuinely cannot be one prompt. Everything else is shared.

### Two modes

**Reactive** — 1–3 steps. Answers, points, single clicks, lookups. Most traffic.

**Deliberate** — the planner engages for long-horizon work. Entered when the
router returns `plan`. Jev also scored a `complexity` number; a chat model
asked for one returns a number that means nothing, so the intent decides.

### The planner: plan is state, not context

This is the single most important design decision for long tasks.

```python
class Step(TypedDict):
    id: int
    action: str
    status: Literal["pending", "running", "done", "failed"]
    result_ref: str | None     # pointer, not the payload
    attempts: int

class Plan(TypedDict):
    goal: str
    steps: list[Step]
    cursor: int
```

Each step executes with a **fresh, minimal context**: the goal, the current
step, the previous result, and only the screen/file context that step needs. Not
the accumulated history.

| Property | Why it follows |
|---|---|
| Bounded tokens | context stays flat as the task grows |
| Resumable | LangGraph checkpointing survives a restart mid-task |
| Visible | cat shows "step 3 of 7", not an opaque spinner |
| Replannable | a failed step triggers a replan, not a blind retry |

A free-running ReAct loop degrades over long horizons — context grows, the model
loses the thread, errors compound. An explicit plan is the fix.

---

## Specialists

Specialists are **tools, not peers**. The harness calls them like any primitive;
they return data, never control.

```python
@tool
def research(query: str) -> Findings: ...     # web only
@tool
def explain(concept: str) -> VideoPath: ...   # manim pipeline
```

Three things follow: composition is free (`research()` then `make_pptx()`), the
security boundary is real (web-reading never touches the filesystem), and the
planner sequences them as ordinary steps.

The router may shortcut directly to a specialist when intent is unambiguous
("explain eigenvectors") to save a hop.

### Research

Split out **for security, not tidiness.** It ingests attacker-controllable web
content, so it gets `search` + `fetch` and nothing else. No files, no desktop,
no send. See [03-safety.md](03-safety.md).

### Explainer

Split out because **it is a pipeline, not an agent.** Manim has a known fixed
shape: plan → code → lint → render → repair → play. When the shape is known,
constraining it beats letting a general agent rediscover it every time. It also
carries its own RAG corpus, its own lint step, and a 30–60s latency profile that
nothing else shares.

---

## Primitives

~19 tools. Every capability in [00-scope.md](00-scope.md) composes from these.
There is no Word-specific or PowerPoint-specific code anywhere.

```
desktop   launch_app · find_element · invoke · click · type_text · key
          read_window · wait_for
files     read_file · write_file · find_files · make_docx · make_pptx
          make_xlsx · open_with
web       search · fetch                      (research specialist only)
verify    screenshot · look_at
escape    delegate_to_cli                     (claude / opencode / codex)
```

Worked examples — note neither needs bespoke code:

```
"open word and write a letter to my landlord"
  launch_app("winword") → find_element("document body") → type_text(...)
  → screenshot → look_at("does this read like a letter?") → done

"make me slides on renewable energy"
  research(...) → make_pptx(outline) → open_with(...)
  → screenshot → look_at("any text overflowing?") → repair → done
```

### Two routes to any document

| | Route A — drive the app | Route B — generate the file |
|---|---|---|
| How | UIA into Word/PowerPoint | python-docx / python-pptx / openpyxl |
| Good at | the user's open document, visible, teachable | complex formatting, large tables, speed |
| Exercises | the grounding thesis | nothing novel |

**Rule:** Route A when the user is watching or names an app. Route B when the
artifact is the point — then `open_with` it so it still lands in Word.

Nobody wants to watch a cat type a 40-row table one keystroke at a time.

---

## The verification loop

Every artifact goes through the same loop. This is the output-side thesis.

```
produce → render/act → screenshot → look_at → repair (max N) → done
```

Word doc, slide deck, webpage, Manim video, GUI action — one mechanism. Most
agents cannot check their own output. Meow already has eyes.

---

## Extensibility: recipes, not code

A new capability is a **retrieved paragraph**, not a release:

> *"To add a slide in PowerPoint: Insert ribbon → New Slide. The title
> placeholder has AutomationId `Title 1`…"*

RAG over a recipe folder — the same technique
[TheoremExplainAgent](https://arxiv.org/html/2502.19400) used over Manim docs.
The capability set is open-ended by construction.

---

## Screen context is a service, not an agent

Making it an agent would add an LLM hop to every interaction and blow the
latency budget. It is synchronous and model-free:

- UIA digest (filtered, capped — see [02-grounding.md](02-grounding.md))
- screenshot, multi-monitor, cursor-screen first
- active app + window title + selected text
- **workspace resolution** — if the active window is an IDE, pull the project
  path from the title bar and UIA

That last one matters: it means "build me a login page" already has a working
directory, and a delegated coding agent does not have to burn tokens
rediscovering which files are open.

Snapshot fires on **key-down**, not key-up — capture happens for free while the
user is still talking.
