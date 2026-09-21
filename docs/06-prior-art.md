# Prior Art

Read from source, not from press coverage. Everything here is verified against
the actual repositories or vendor documentation.

---

## Clicky (the original)

[github.com/farzaa/clicky](https://github.com/farzaa/clicky) — MIT, macOS,
Swift/SwiftUI with AppKit bridging. Farza Majeed stopped open-sourcing on
**27 April 2026**; new work is private at heyclicky.com. The repo is the v1
prototype.

### The full pipeline

```
hold ctrl+option
  → capture all monitors (ScreenCaptureKit, 1280px max edge, JPEG q0.8,
    own windows excluded, cursor's screen sorted first)
  → AssemblyAI streaming STT over websocket
  → Claude Sonnet 4.6 (screenshot + last 10 turns) via Cloudflare Worker
  → SSE stream
  → ElevenLabs Flash v2.5 TTS
  → blue triangle flies a bezier arc to the target
```

### What it can actually do

**Five things.** Hear you, see your screens, answer in voice, point at *one*
element, remember 10 turns.

**No clicking. No agents. No code execution.** Those are commercial-only. So for
Meow's action layer there is no reference implementation to translate — it is
designed from scratch.

### The pointing mechanism

Much simpler than it looks, and that is the insight. No vision grounding model,
no computer-use API. The model appends a text tag and a regex extracts it:

```
[POINT:x,y:label:screenN]     or     [POINT:none]

\[POINT:(?:none|(\d+)\s*,\s*(\d+)(?::([^\]:\s][^\]:]*?))?(?::screen(\d+))?)\]\s*$
```

~30 lines of system prompt does the rest. **This is Meow's vision baseline** —
reproduce it faithfully for the ablation in [04-evaluation.md](04-evaluation.md).

### Worth stealing

- **Keys live in a Cloudflare Worker proxy** (`/chat`, `/tts`,
  `/transcribe-token`). The app binary ships zero secrets.
- **Exclude own windows from capture** — otherwise the assistant sees itself.
  Windows equivalent is `WDA_EXCLUDEFROMCAPTURE`.
- **TLS warmup** on the Claude client to cut first-token latency.
- **The prompt is the product.** All lowercase, "write for the ear, not the
  eye", never say "simply" or "just", and never end on a yes/no question because
  those are "dead ends that force the user to just say yes."
- A single long-lived `URLSession` shared across STT sessions — creating one per
  session corrupts the OS connection pool.

### Commercial HeyClicky

$20/mo Pro, $100/mo Max, free tier. Adds clicking, dictation, and background
agents ("heyclicky agent"). macOS only, **Windows version anticipated.**

---

## Existing Windows ports

| Port | Stack | State |
|---|---|---|
| [clickyX](https://github.com/unn-Known1/clickyX) | Rust + Tauri v2 + React 19 | Most complete. enigo control, 63 skills via Codex sidecar, multi-provider STT/TTS, bridge API. 22★, v0.2.0 |
| [clicky_windows](https://github.com/emreyilmaz46/clicky_windows) | .NET 8 + WPF | Faithful port. GDI capture, `RegisterHotKey`, NAudio. No agents. 20★, 7 commits |
| MatheeshaAI / Bitshank / AbhisumatK | Python + PyQt6 + Ollama | Offline, no API key, forks of each other |

None combine reliable action, planning, and an open-ended capability set. ClickyX
is worth reading; forking it means inheriting its architecture.

---

## Reference systems

**[Windows-Use](https://github.com/CursorTouch/Windows-Use)** — reads the screen
via UIA rather than vision, uses pywinauto + pyautogui, ~25 step default cap.
Tells users to run it in a VM. The closest existing thing to Meow's action layer.

**[TheoremExplainAgent](https://arxiv.org/html/2502.19400)** — planner agent
writes storyboard + narration, coding agent writes Manim, TTS renders voice.
**93.8% success** over 240 theorems. Key technique: **RAG over Manim docs at
three stages** — storyboard, implementation, *and error correction*. The
blueprint for Phase 3.

**[ManiBench](https://arxiv.org/html/2603.13251v1)** — benchmarks LLM Manim
generation. Two failure modes: *syntactic hallucination* (valid Python calling
non-existent APIs) and *visual-logic drift*. Documents **145 ManimGL→ManimCE
incompatibilities**.

**[manim-voiceover](https://voiceover.manim.community/)** — official plugin.
Auto-syncs animation duration to narration via `tracker.duration`, supports
ElevenLabs, does bookmark-based per-word timing. Solves narration sync as an
install rather than a build.

---

## Platform facts

| | |
|---|---|
| `WDA_EXCLUDEFROMCAPTURE` | Win10 2004+. DWM excludes the window from all capture surfaces |
| `WH_KEYBOARD_LL` | **stops delivering events when a Chromium window has focus** |
| Electron `globalShortcut` | no key-up event; infers release from auto-repeat (~1.1s stuck tap) |
| Chromium accessibility | **Not lazy in practice.** Chrome and VS Code both expose full trees to a plain UIA client — no flag, no relaunch. Electron nests content ~30 levels deep, which is what shallow walks mistake for an empty tree. Measured, see [02-grounding.md](02-grounding.md) |
| Per-monitor DPI | without `PER_MONITOR_AWARE_V2`, `SendInput`/`GetCursorPos` are DPI-virtualized |
| Everything (`es.exe`) | NTFS change-journal index, instant search. winget: `voidtools.Everything.Cli` |

## Vendor facts

| | |
|---|---|
| Computer use toolset | `computer_toolset_20260801`, 17 actions. ~1,000–1,800 tokens per screenshot; keep ≤20 images, prune to last 3 |
| Jev | `langchain-typesafe` — real, but **pre-release** (0.0.1a3), so `pip index` cannot see it and a plain install is needed. `Choice.criteria` is a **mapping** of option to description, not a list of names; `Score.criteria` is a list. Parallel questions ≈ free. API, not local |
| Jev keys | Must be native, from typesafe.ai. A Vercel AI Gateway key (`vck_...`) **does not work**: the gateway is OpenAI-compatible for chat completions, and Jev's System One endpoint is bespoke. Measured — api.typesafe.ai returns 401 for the key, the gateway returns 404 for `/v1/systemone`, and no `base_url` bridges the two |
| `create_agent` | replaces deprecated `create_react_agent`; middleware system is where the risk gate plugs in |
| Composio | `composio-langgraph`, managed OAuth, 250+ (to 1,500+) integrations |
| STT latency | Deepgram Flux / ElevenLabs Scribe v2 lowest; AssemblyAI ~300ms+; local Whisper ~500ms |
| TTS latency | **Measured, not quoted.** ElevenLabs Flash v2.5 reaches first audio in **394ms median** from India (requests land in asia-southeast1); the advertised ~75ms is inference only. Kokoro-82M on this CPU: **1714ms median**, and 0.4-1.2x realtime. Kokoro is not slower at generating - it is slower to the *first sound*, because it returns a finished clip while ElevenLabs streams |
