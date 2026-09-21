# Meow — Agent Instructions

<!-- Single source of truth for AI coding agents working on this repo. -->
<!-- CLAUDE.md should symlink to or mirror this file. -->

## What this is

A voice-driven desktop companion for Windows. A cat lives on screen, hears you,
sees what you see, and **operates your machine with you** — narrating, and
asking before anything that matters.

Inspired by [Clicky](https://github.com/farzaa/clicky) (macOS, MIT). Not a port:
Clicky points at things and explains them; Meow does them.

Personal study project. One developer, part-time. **Scope is the primary risk.**

## Read first

| Doc | For |
|---|---|
| [docs/00-scope.md](docs/00-scope.md) | goals, non-goals, constraints, open questions |
| [docs/01-architecture.md](docs/01-architecture.md) | harness, specialists, modes, primitives |
| [docs/02-grounding.md](docs/02-grounding.md) | UIA vs vision — **the core thesis** |
| [docs/03-safety.md](docs/03-safety.md) | lethal trifecta, tool isolation — **read before adding any tool** |
| [docs/04-evaluation.md](docs/04-evaluation.md) | the ablation study |
| [docs/05-phases.md](docs/05-phases.md) | build order |
| [docs/06-prior-art.md](docs/06-prior-art.md) | verified facts about Clicky, ports, platform APIs |

## Status

**Phase 0, not started.** Two spikes, both run, and the headline result reversed
under investigation:

- `spikes/uia_probe.py` — **everything on this machine is RICH.** VS Code 819
  actionable elements, Chrome 153, Explorer 74.
- The earlier "VS Code is SKELETAL (6 elements)" result was a bug in the walker,
  not a platform limit: `MAX_DEPTH = 12` cut off content that Electron nests ~30
  levels deep. Cap is now 50.
- `spikes/wake_probe.py` — `SPI_SETSCREENREADER` does nothing, and neither does
  `editor.accessibilitySupport`. Both A/B tested. **No wake step is needed.**

**UIA grounding works on Electron.** The open problem moved from availability to
selection: 819 actionable elements against a ~150 digest budget, and a 1.18s
full walk that does not fit a voice loop. See
[docs/02-grounding.md](docs/02-grounding.md) § The real problem, relocated.

## Stack

| | |
|---|---|
| Language | Python 3.12 (already installed) |
| Agent framework | LangGraph + `create_agent` (**not** deprecated `create_react_agent`) |
| Tracing | LangSmith — a requirement, not optional |
| Router | Jev via `langchain-typesafe` — non-generative classifier |
| UIA | `uiautomation` |
| Win32 | `pywin32` |
| Connectors | `composio-langgraph` (Phase 4) |
| Voice | provider interface; cloud vs local deferred |

Target machine is **CPU-only** — no local GPU inference. Not yet installed:
`ffmpeg` (needed for Phase 3), `uv`, `codex`, `aider`.

## Architecture in one picture

```
Jev (reflex: route · risk · complexity — non-generative)
 │
 ├──▶ RESEARCH   search + fetch only. No files. No desktop. No send.
 ├──▶ HARNESS    ~19 primitives · 2 prompts · reactive | deliberate(planner)
 └──▶ EXPLAINER  manim pipeline, fixed shape
```

## Invariants

Do not violate these without updating the relevant doc first.

1. **No new agents.** Capability grows through tools and recipes. If you are
   about to add an agent, add a tool or a recipe instead.
2. **No component gets all three of:** private data · untrusted content ·
   external send. See [docs/03-safety.md](docs/03-safety.md).
3. **Grounding stays swappable.** `VisionGrounding` / `UIAGrounding` /
   `HybridGrounding` behind one protocol — the evaluation depends on it.
4. **Plan is state, not context.** Long tasks must not accumulate into one
   growing conversation.
5. **Panic path is local.** No network, no Jev, no model on the abort route.
6. **`run_powershell` always confirms.** No exceptions, no "remember this".
7. **The overlay is excluded from capture** (`WDA_EXCLUDEFROMCAPTURE`) — or the
   cat appears in its own screenshots and confuses the model.
8. **Coordinates:** call `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`
   at startup, or clicks land off-target on secondary monitors.
9. **No push-to-talk chord.** Held two-key chords are hostile to tremor and
   arthritis. Wake word or single key.

## Known platform traps

- **A truncated walk is not a regime.** If a depth cap, time budget or element
  cap was hit, the tree was not seen — report `TRUNCATED`, never `SKELETAL` or
  `EMPTY`. Classifying a cut-short walk as a platform limitation cost this
  project two days and produced a spike for a problem that did not exist.
- **Electron nests content ~30 levels deep.** Anything walking a UIA tree needs
  real depth headroom. VS Code's deepest actionable elements sit at depth 39.
- `WH_KEYBOARD_LL` **stops firing when a Chromium window has focus** (Chrome,
  VS Code, Slack). Needs a `RegisterHotKey` fallback.
- **Minimized windows cannot be walked** — they report a zero-size rect.
  Occluded windows read fine; minimized ones do not. Report them, never drop
  them silently, or an absent app reads as a negative result.
- **Walk time is unstable.** Chrome varied 0.27s → 1.12s on the same window,
  same element count. VS Code at full depth is 1.18s. Budget for the worst case
  and return partial results on a deadline; never walk a window whole inside the
  voice loop.
- Virtual desktop coordinates go **negative** left of / above the primary monitor.

## Conventions

- Clarity over concision in names. `originalQuestionLastAnsweredDate`, not
  `originalAnswered`. No single-character variables.
- Comments explain **why**, not what — especially around Win32 interop.
- Voice output: lowercase, conversational, no markdown, no lists. Written for
  the ear. Never "simply" or "just". Never end on a yes/no question.
- Every tool that touches the filesystem or sends anything declares its risk
  level explicitly.

## Self-update

When a change affects what is documented here, update this file and the relevant
doc in the same commit. Specifically: new invariants, stack changes, phase
completion, and any resolution of an open question in
[docs/00-scope.md](docs/00-scope.md).
