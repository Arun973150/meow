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

**Phase 0 in progress. 0.1 through 0.5 done and verified.**

```
meow/platform/dpi.py        PER_MONITOR_AWARE_V2, with two fallbacks
meow/platform/monitors.py   virtual desktop, negative origins, per-monitor rects
meow/platform/overlay.py    layered - click-through - topmost - no-activate
meow/platform/capture.py    BitBlt to BGRA + per-monitor JPEG, cursor screen first
meow/platform/hotkey.py     RegisterHotKey activation, single key, NOREPEAT
meow/cat/sprite.py          line-art cat, traced from image.png
meow/cat/animation.py       7 states, cross-faded
meow/cat/follow.py          critically damped cursor follow
meow/cat/bubble.py          small speech bubble - capped, never a transcript
meow/config.py              .env keys; never logs a value
meow/vision.py              when to send a screenshot, and how much of one
meow/mind.py                gpt-4o-mini call, streamed, sentence-chunked
meow/console.py             UTF-8 stdout - cp1252 cannot print what STT returns
meow/voice/microphone.py    16kHz mono PCM16, bounded queue, RMS level
meow/voice/stt.py           Transcriber protocol + AssemblyAI v3 streaming
meow/voice/tts.py           Speaker protocol + ElevenLabs eleven_flash_v2_5
scripts/overlay_demo.py     A/B tests the capture exclusion
scripts/cat_demo.py         the cat, live, cycling states
scripts/cat_preview.py      all states to one PNG, light and dark
scripts/companion_demo.py   tap Ctrl+M, the cat wakes and follows the cursor
scripts/listen_demo.py      tap Ctrl+M and talk - words appear in the bubble
scripts/check_keys.py       what is installed, which keys are set
scripts/meow.py             THE WHOLE LOOP - talk to it and it answers
```

Activation is `RegisterHotKey`, **not** `WH_KEYBOARD_LL` - the hook stops
delivering while a Chromium window has focus, and activation that dies in
Chrome and VS Code is worse than none. Single key, `MOD_NOREPEAT`.

Verified: `WDA_EXCLUDEFROMCAPTURE` applied, **0 overlay pixels in our own
screenshot with it on, 16929 with it off.** Invariant 7 holds, and the control
run proves the capture path was working rather than returning black.

The cat is drawn procedurally, not loaded from sprite sheets - expressions are
numbers, so states cross-fade and the gaze can aim anywhere. It samples the
screen beneath itself and inverts its ink over dark windows, which is only
possible because the overlay is excluded from capture.

Spikes, both run:

- `spikes/uia_probe.py` - **everything on this machine is RICH.** VS Code 819
  actionable elements, Chrome 153, Explorer 74.
- The earlier "VS Code is SKELETAL (6 elements)" result was a bug in the walker,
  not a platform limit: `MAX_DEPTH = 12` cut off content that Electron nests ~30
  levels deep. Cap is now 50.
- `spikes/wake_probe.py` - `SPI_SETSCREENREADER` does nothing, and neither does
  `editor.accessibilitySupport`. Both A/B tested. **No wake step is needed.**

**0.6, 0.7 and 0.9 done. `python scripts/meow.py` is the whole loop:** tap
Ctrl+M, talk, and the cat answers out loud while seeing your screen.

Measured end to end: ~2.5s to the first spoken sentence, ~$0.0005 per turn with
an image. Sentence-chunked, so speech starts before the reply is finished.

Left in Phase 0: **0.8**, the `[POINT:x,y]` baseline — which is also the control
condition for the ablation in [docs/04-evaluation.md](docs/04-evaluation.md).
Then Phase 1 and the UIA digest, which is the actual thesis.

## Stack

| | |
|---|---|
| Language | Python 3.12 (installed) |
| Agent framework | LangGraph + `create_agent` (**not** deprecated `create_react_agent`) |
| Model | **OpenAI `gpt-4o-mini`** — cost-constrained, see `meow/vision.py` |
| STT | **AssemblyAI v3 streaming** — `wss://streaming.assemblyai.com/v3/ws` |
| TTS | **ElevenLabs Flash v2.5** — `eleven_flash_v2_5` |
| Tracing | LangSmith — a requirement, not optional |
| Router | Jev via `langchain-typesafe` — non-generative classifier |
| UIA | `uiautomation` (installed) |
| Win32 | `pywin32` (installed) |
| Connectors | `composio-langgraph` (Phase 4) |

Target machine is **CPU-only** — no local GPU inference. Not yet installed:
`ffmpeg` (needed for Phase 3), `uv`, `codex`, `aider`.

**Latency rules that follow from this stack:**

- STT must **stream**. Jev routes on interim transcripts, so text has to arrive
  while the user is still talking.
- Fire the model on AssemblyAI's `end_of_turn`, **not** on `turn_is_formatted`.
  Formatting arrives later and buys nothing the model needs.
- Keys never ship in the client. Clicky proxies through a Cloudflare Worker and
  fetches a short-lived AssemblyAI token per session; copy that shape.

`reference/clicky/` is a local clone of the original (gitignored). Worth reading:
`AssemblyAIStreamingTranscriptionProvider.swift`, `ElevenLabsTTSClient.swift`,
`CompanionScreenCaptureUtility.swift`, `ElementLocationDetector.swift`.

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
9. **Never send a full-screen `detail=high` image.** 36,835 tokens against
   2,833 for low detail, which is *flat* regardless of resolution. Send nothing,
   or "unchanged", or low detail at full size, or a 512px high-detail crop. See
   `meow/vision.py`.
10. **Activation is tapped, never held.** The problem with Clicky's ctrl+option
   is that it is *sustained* for the length of an utterance, which is hostile to
   tremor and arthritis - not that it has two keys. A tapped chord that toggles
   is fine. Use `RegisterHotKey`, never `WH_KEYBOARD_LL`: the hook dies under
   Chromium focus, and only the former keeps **Sticky Keys** working, which is
   what lets someone press Ctrl then M in sequence.

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
- **Every GDI handle needs an explicit ctypes `restype` on 64-bit Python.**
  Unset, it truncates to 32 bits and surfaces as "OverflowError: int too long to
  convert" several calls later, pointing at innocent code.
- **The Windows console is cp1252 and cannot print what a speech API returns.**
  Formatted transcripts carry curly quotes and ellipses, and printing one raises
  `UnicodeEncodeError` *in the print*, so the traceback blames innocent code.
  Call `meow.console.use_utf8_console()` first.
- **Closing the AssemblyAI socket takes ~1s** - it is a termination handshake,
  not a socket close. Never do it on the render thread.

## Conventions

- Clarity over concision in names. `originalQuestionLastAnsweredDate`, not
  `originalAnswered`. No single-character variables.
- Comments explain **why**, not what — especially around Win32 interop.
- Voice output: lowercase, conversational, no markdown, no lists. Written for
  the ear. Never "simply" or "just". Never end on a yes/no question.
- **The speech bubble is not a transcript.** Voice is the primary channel; the
  bubble is a glanceable cue, hard-capped at 90 characters and 3 lines. If it
  ever grows to hold whole replies, the cat has become a chat window.
- Every tool that touches the filesystem or sends anything declares its risk
  level explicitly.

## Self-update

When a change affects what is documented here, update this file and the relevant
doc in the same commit. Specifically: new invariants, stack changes, phase
completion, and any resolution of an open question in
[docs/00-scope.md](docs/00-scope.md).
