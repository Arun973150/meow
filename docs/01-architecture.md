# Architecture

One harness, two specialists, two modes. Capability grows through tools and
recipes — never through new agents.

```
                    Jev   reflex: route · risk · complexity   (non-generative)
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

## Jev — the reflex layer

[Jev](https://www.langchain.com/blog/building-a-harness-with-jev) (TypeSafe) is a
"System One" model: it classifies, scores and routes but **cannot generate text**.
Reportedly 40–400× cheaper than a small frontier LLM on classification.

One call answers every routing question at once — parallel questions barely
change response time:

```python
from langchain_typesafe import TypeSafeClassifier, Choice, Noul, Score

route = classifier.invoke({
    "state": transcript + screen_digest,
    "questions": {
        "intent":       Choice(options=["answer", "point", "act", "research",
                                        "build", "explain", "dictate", "abort"]),
        "destructive":  Noul(instructions="Would this delete, send, pay, or overwrite?"),
        "needs_screen": Noul(instructions="Does answering require seeing the screen?"),
        "complexity":   Score(instructions="How hard is this task?"),
    }
})
```

| Output | Drives |
|---|---|
| `intent` | which path runs |
| `destructive` | the confirmation gate ([03-safety.md](03-safety.md)) |
| `needs_screen` | skip vision tokens on general questions |
| `complexity` | reactive vs deliberate mode |

**Jev is an API call, not local.** Budget 30–80ms RTT. The panic-abort path must
never depend on it — that stays local keyword matching.

Because it returns *calibrated probabilities*, low confidence is actionable: the
cat asks one short clarifying question rather than misrouting. This matters —
"can you fix this login bug" (spawn a build) and "how would I fix this login
bug" (just answer) are semantically adjacent and functionally opposite.

### The latency trick

Jev is cheap enough to run on **every interim transcript** while the user is
still speaking. By the time they stop talking, routing is already decided.

```
serial   STT 250ms → route 80ms → capture 200ms → LLM 450ms → TTS 120ms  = ~1100ms
parallel STT 250ms → [route and capture already done] → LLM 450 → TTS 120 = ~820ms
```

This is only affordable because Jev is not a generative model. You could not do
this with a small LLM.

---

## The harness

`create_agent` from `langchain` (note: `create_react_agent` is deprecated). Its
**middleware system** is where Jev's risk gate plugs in, which is why the agent
loop, the safety gate, and LangSmith tracing all share one mechanism.

### Two prompts, not two agents

| Prompt | Used for |
|---|---|
| **voice** | conversational replies. Write for the ear — lowercase, no markdown, no lists, short sentences |
| **work** | tool-calling. Structured, terse, no personality |

These genuinely cannot be one prompt. Everything else is shared.

### Two modes

**Reactive** — 1–3 steps. Answers, points, single clicks, lookups. Most traffic.

**Deliberate** — the planner engages for long-horizon work. Entered when Jev's
`complexity` crosses threshold.

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
