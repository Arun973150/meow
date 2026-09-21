# Meow — Project Scope

A voice-driven desktop companion for Windows. A small cat lives on screen,
hears you, sees what you see, and operates your machine with you.

Inspired by [Clicky](https://github.com/farzaa/clicky) (macOS, MIT), but not a
port — Clicky points at things and explains them. Meow does them.

---

## What it is

Hold a key (or say the wake word), ask for something, and the cat answers out
loud. If the thing you want is on screen, it goes and does it, narrating as it
goes and asking before anything that matters.

    "where's my resume?"            -> finds it, tells you
    "what does this button do?"     -> explains, points at it
    "open word and write a letter
     to my landlord about the leak" -> opens Word, types it, shows you
    "find research on solar costs
     and put it in a spreadsheet"   -> searches, builds the file, opens it
    "make me slides on this"        -> generates the deck, checks it looks right
    "i don't get eigenvectors"      -> explains, then animates it

The capability list is deliberately open-ended. These are examples of what the
primitives compose into, not a menu of hardcoded skills.

## Who it is for

Primary target is general use. But the design constraints come from the harder
case: **people with motor or vision impairments, and older users.**

This is not charity framing, it is engineering discipline. Designing for someone
who cannot easily verify or undo a wrong action forces confirmable steps, clear
narration, real undo, and no silent failures — which makes the product better
for everyone. Voice interfaces lift task completion by more than 40% for users
with motor impairments *when designed for them*.

One concrete consequence: **Clicky's held two-key chord (ctrl+option) is not
acceptable.** A sustained modifier chord is hostile to tremor and arthritis.
Meow uses a wake word or a single key.

## Why it exists

This is a personal study project. It is not competing with anything. But it
needs a defensible technical contribution, and it has two:

**1. Grounding actions in the accessibility tree rather than in pixels.**
Clicky asks a vision model to guess `(x, y)` from a screenshot. Meow asks the
operating system where things are, and invokes them by name. This is
Windows-specific — macOS's accessibility API is weaker, so it is not something
the original could simply port.

**2. Visual self-verification of everything it produces.**
Word doc, slide deck, webpage, Manim render, GUI action — all go through the
same loop: produce it, look at it, repair it. The agent checks its own work the
way a person would, by looking. It already has eyes for other reasons.

See [02-grounding.md](02-grounding.md) and [04-evaluation.md](04-evaluation.md).

## Non-goals

- **Not a heavy coding agent.** Simple artifacts only. Anything large delegates
  to the user's own Claude Code / opencode / Codex. See [05-phases.md](05-phases.md).
- **Not a background automation tool.** Meow works *with* you, in your session,
  while you watch. Delegate-and-walk-away is a different product.
- **Not multi-agent.** One harness, two specialists. Capability grows through
  tools and recipes, not through new agents.
- **Not cross-platform.** Windows only. The whole thesis depends on Windows UIA.

## Constraints

| | |
|---|---|
| Target machine | Windows 11, **CPU only** — no local GPU inference |
| Latency | ~800ms to first spoken syllable. Sub-500ms is not reachable with cloud LLM + cloud TTS |
| Agent framework | LangGraph + LangSmith (tracing is a requirement, not a nice-to-have) |
| Router | Jev (TypeSafe) — non-generative classifier |
| Language | Python — every dependency below is Python-native |
| Build capacity | One person, part-time, alongside studies |

**Scope is the main risk on this project, not architecture.** Phases exist to be
cut. Everything past Phase 1 is optional.

## Open questions

| Question | Blocks | Status |
|---|---|---|
| Does UIA expose enough of real apps? | the core thesis | **Resolved — yes.** VS Code 819 actionable elements, Chrome 153, Explorer 74. All RICH at full depth |
| Can a Chromium tree be woken reliably? | Chrome/VS Code/Slack support | **Resolved — no wake needed.** The skeletal reading was a depth-cap bug. `SPI_SETSCREENREADER` and `editor.accessibilitySupport` both A/B tested, neither did anything |
| Which ~150 of 819 elements does the model see? | every action in Phase 1 | **Open, and now the hard problem.** Replaces the two above |
| Cloud or local voice? | latency, cost, privacy | Partly settled by architecture: Jev routes on *interim* transcripts, which requires **streaming** STT, which rules out batch local Whisper on the hot path. TTS can still be local |
| Bundle Manim deps or guided install? | Phase 3 onboarding | deferred to Phase 3 |

## Document map

| | |
|---|---|
| [01-architecture.md](01-architecture.md) | harness, specialists, modes, data flow |
| [02-grounding.md](02-grounding.md) | UIA vs vision, the regimes, the thesis |
| [03-safety.md](03-safety.md) | lethal trifecta, confirm gates, tool isolation |
| [04-evaluation.md](04-evaluation.md) | the ablation study and its metrics |
| [05-phases.md](05-phases.md) | build order |
| [06-prior-art.md](06-prior-art.md) | what Clicky does, what the ports do, what to steal |
