# Phases

Each phase ends with something demonstrable. Everything past Phase 1 is
optional — **scope is the main risk on this project.**

---

## Phase 0 — Spine

*Goal: the loop works end to end. Roughly Clicky parity.*

The dependency chain here is unglamorous plumbing, and nothing else works until
all five are right:

```
overlay → hide-from-capture → capture → coordinates → DPI
```

| | Task | Notes |
|---|---|---|
| 0.1 | Transparent click-through overlay, all monitors | **Done.** `WS_EX_LAYERED \| TRANSPARENT \| TOPMOST \| NOACTIVATE` |
| 0.2 | **Hide overlay from its own screenshots** | **Done, A/B verified** — 0 overlay pixels with `WDA_EXCLUDEFROMCAPTURE`, 16929 without |
| 0.3 | Multi-monitor capture, labeled, cursor-screen first | **Done.** 1280px max edge, JPEG q80. Measured: 8.8 MB raw -> 63 KB. `ScreenShot.to_screen()` carries the scale back, which 0.8 needs |
| 0.4 | DPI-correct coordinate pipeline | **Done.** `PER_MONITOR_AWARE_V2` — prerequisite for every click |
| 0.5 | Cat sprite with animation states | idle · listening · thinking · speaking · pointing · working · sleeping. **Done** - line art, 7 states, follows the cursor when activated |
| 0.6 | **Done.** Voice loop: activation → STT → OpenAI → TTS | AssemblyAI v3 streaming in, ElevenLabs `eleven_flash_v2_5` out. Fire the model on `end_of_turn`, not `turn_is_formatted` |
| 0.7 | **Done.** Sentence-chunked TTS | speak sentence 1 while writing sentence 2 — biggest perceived-latency win |
| 0.8 | Point at an element | the `[POINT:x,y]` baseline — also Claim 1's control condition |
| 0.9 | **Done.** Conversation history | last 10 turns |

**Demo:** *"ask it what a button does — the cat flies over and explains."*

⚠ **Activation is tapped, never held.** The problem with a push-to-talk chord is
that it is *sustained* for the whole utterance, which is hostile to tremor and
arthritis — not that it has two keys. A tapped toggle is fine. Currently
**Ctrl+M**. See [00-scope.md](00-scope.md).

⚠ `WH_KEYBOARD_LL` **stops firing when a Chromium window has focus** — Chrome,
VS Code, Slack. Electron's `globalShortcut` has no key-up at all. **Resolved:**
activation uses `RegisterHotKey`, which is immune to both and is also the only
option that keeps Sticky Keys working.

---

## Phase 1 — Grounding and the harness

*Goal: it does things instead of pointing at them. This is the thesis.*

| | Task |
|---|---|
| 1.1 | UIA tree extraction — filtered, capped at ~150 elements |
| 1.2 | `Grounding` protocol with all three strategies |
| 1.3 | Regime detection (RICH / DENSE / EMPTY) — and `TRUNCATED` as a refusal to classify |
| 1.4 | **Element selection under a ~150 budget** — was "Chromium wake techniques"; no wake is needed, but VS Code exposes 819 actionable elements and the model can see ~150 |
| 1.5 | `create_agent` harness, reactive mode, ~19 primitives |
| 1.6 | Jev router on interim transcripts |
| 1.7 | Confirmation gate via `AutoModeMiddleware` |
| 1.8 | Panic key — local, no network |
| 1.9 | Visual verification loop: produce → screenshot → `look_at` → repair |
| 1.10 | **Evaluation harness + 30-task suite** |

**Demo:** *"it uses your computer for you, while you watch, and asks before
anything that matters."*

**Prerequisite met.** Both spikes have run. Chrome, VS Code and Explorer all
come back RICH at full depth — the Electron risk that 1.3 and 1.4 were hedging
against does not exist. No wake step is needed.

The work did not shrink, it moved: a full VS Code walk is 1.18s and yields 819
actionable elements against a ~150-element digest. **1.1 and 1.4 are now the
hard parts** — scoped subtree queries instead of whole-window walks, and
choosing which elements the model actually sees.

---

## Phase 2 — Planner and knowledge work

*Goal: multi-step tasks that produce files you keep.*

| | Task |
|---|---|
| 2.1 | Planner mode — plan as state, checkpointed |
| 2.2 | Progress on the cat ("step 3 of 7") |
| 2.3 | Research specialist — `search` + `fetch`, isolated toolset |
| 2.4 | Document primitives — `make_docx` / `make_pptx` / `make_xlsx` |
| 2.5 | **Search-augmented pointing** — look up how, *then* point |
| 2.6 | Recipe folder + retrieval |

2.5 is a genuine improvement over Clicky, which can only point from model
memory.

**Demo:** *"find research on solar costs, put it in a spreadsheet, open it."*

---

## Phase 3 — Explainer

*Goal: the encore. Highest demo impact, highest cost.*

| | Task |
|---|---|
| 3.1 | Dependencies — ffmpeg (**not installed**) + MiKTeX (500MB–2GB) |
| 3.2 | Template library — ~20 verified parameterized scenes |
| 3.3 | Static lint against the installed module, before rendering |
| 3.4 | Render pipeline — `-pql` preview first |
| 3.5 | Repair loop — stderr + RAG over Manim docs, max 3 |
| 3.6 | `manim-voiceover` for narration sync |
| 3.7 | Playback in the overlay |

The lint step is free and catches the dominant failure mode:

```python
import manim, ast
REAL_API = set(dir(manim))
# `ShowCreation` is ManimGL, not ManimCE — caught in milliseconds
# instead of after a 30-second render
```

There are **145 documented ManimGL→ManimCE incompatibilities**, and models mix
them constantly because both are called "manim" in training data. Pin ManimCE
hard and inject the deprecation map into the prompt.

⚠ The verbal answer must not wait for the render. The cat explains in speech
immediately (<1s), says "let me draw that", and the animation appears when
ready.

---

## Phase 4 — Connectors

*Goal: "summarize my messages."*

| | Task |
|---|---|
| 4.1 | Composio via `composio-langgraph` — managed OAuth |
| 4.2 | **READER specialist** — gmail/calendar/slack/youtube read |
| 4.3 | **SENDER specialist** — send/post/create, always confirmed |
| 4.4 | Handoff between them goes through the user |

The READER/SENDER split is non-negotiable — see
[03-safety.md](03-safety.md).

---

## Phase 5 — Delegation

*Goal: heavy coding, without building a coding agent.*

| | Task |
|---|---|
| 5.1 | `delegate_to_cli` — tool #19 |
| 5.2 | JSONL stream parser → cat narrates progress |

```
claude   claude -p "<task>" --output-format stream-json
codex    codex exec --json "<task>"
```

Already on this machine: `claude`, `gemini`, `opencode`. Not installed: `codex`.

Heavy coding is where cost, sandboxing risk, and multi-tenancy pain all live.
Delegating means the user brings their own agent, their own auth, and their own
subscription.
