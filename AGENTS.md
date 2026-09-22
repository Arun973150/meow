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
| [docs/07-live-test.md](docs/07-live-test.md) | the twenty-minute spoken test — what only a microphone can check |

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
meow/mind.py                ChatOpenAI, streamed, sentence-chunked
meow/pointing.py            [POINT:x,y] protocol, eased pointer glide
meow/uia.py                 THE THESIS - accessibility tree digest, 268ms
meow/grounding.py           UIA / vision / hybrid behind one protocol
meow/actions.py             point, click, invoke, type - each with a risk level
meow/harness.py             LangGraph create_agent + HumanInTheLoop gate
meow/router.py              Jev via langchain-typesafe, on interim transcripts
meow/evaluation.py          THE ABLATION - UIA labels its own ground truth
meow/jev.py                 Jev via the Vercel gateway, a LangChain Runnable
meow/panic.py               the stop button - local, latched, no network
meow/apps.py                open apps, switch windows, list what exists
meow/planner.py             PHASE 2 - multi-step tasks as a LangGraph state machine
meow/risk.py                when to ask, and when asking is just noise
meow/documents.py           docx / xlsx / pptx into Documents/Meow
meow/research.py            search + fetch ONLY - the trifecta split
meow/queries.py             a spoken sentence -> searches that find something
meow/memory.py              ONE memory - what was said, and who is working
meow/verify.py              PHASE 1.9 - did the action actually happen?
meow/lookup.py              PHASE 2.5 - look up how, then point at the real thing
meow/recipes.py             PHASE 2.6 - a capability is a file, not a release
meow/conversations.py       every conversation, kept - SQLite in Documents/Meow
meow/chat/window.py         the chat window - PySide6, its OWN PROCESS
meow/chat/bubbles.py        painted bubbles - Qt rich text cannot do them
meow/chat/icons.py          icons drawn with QPainter, not shipped as files
meow/chat/launcher.py       starting the window, and recording into it
recipes/                    the shipped recipes; the user's go in Documents/Meow
meow/tasks.py               handed-over work, on its own thread
meow/taskwindow.py          the small window each task gets
meow/cat/cursor.py          cat_cursor.png as the system cursor, restored
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
scripts/smoke.py           runs the REAL app and fails on any traceback
scripts/stress.py          48 edge cases across every module
scripts/meow.py             THE WHOLE LOOP - routes, answers, points, presses
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

**PHASE 0 IS COMPLETE.** `python scripts/meow.py` - tap Ctrl+M, talk, and the
cat answers out loud, sees your screen, and flies the pointer to what you asked
about while wearing `cat_cursor.png`.

Measured: ~2.5s to the first spoken sentence, ~$0.0005 per turn with an image,
394ms to first audio, 634ms for a pointer glide across the screen.

**Phase 1: 1.1, 1.2, 1.4, 1.5 and 1.7 done.** Say "click the close button" and
it happens. The model reads the control list and asks for a control BY NAME,
which resolves against the tree to an exact rectangle - it never produces a
coordinate, so it cannot produce a wrong one. `invoke()` then presses through
UIA with no pointer movement, which works on a window that is not in front.

The harness is `langchain.agents.create_agent` with `HumanInTheLoopMiddleware`
as the confirmation gate — the interrupt is part of the graph rather than a
callback, and the checkpointer that makes it resumable is the same mechanism
Phase 2's planner needs. `ModelCallLimitMiddleware` caps the rounds.

LangSmith turns on by itself if `LANGSMITH_API_KEY` is in `.env`; currently off.

**Phase 1 is complete and reachable by voice.** `python scripts/meow.py` — tap
Ctrl+M and talk, tap **Pause** to stop everything. Jev routes each sentence to
answer / show / act / plan; act goes through the UIA harness and asks out loud
before pressing anything.

It reaches past the window in front: `open chrome` launches it, `switch to my
chrome window` brings it forward, and `press_keys` covers the things that have
no clickable control - ctrl+L for an address bar, Enter to submit a search.
Control names are matched the way people speak them, degrading from exact to
word overlap to close spelling, so "that terminal thing" and "minimise" both
land.

**Phase 1.9 done: actions are checked, not assumed.** The cat used to say
"i typed your name in the document" whether or not a character arrived -
`SendInput` returning means the input queue accepted the keystrokes, not
that a field received them. `meow/verify.py` snapshots the desktop either
side of an action and reports what actually changed.

**Checked through UIA, not a screenshot** - a deviation from the phase plan
that the project's own ablation justifies: vision scored 0/6 at locating
controls, so verifying with pixels would cost 2,833 tokens a turn to
consult the losing strategy. A snapshot is 9ms warm (130-260ms on the first
call, while COM starts), so every action can afford one.

**A verdict is yes, no, or could not tell, and the third matters most.**
Clicking into a text box changes nothing observable; that is not failure.
A verifier that only ever confirms is a more confident liar than none, so
an unverifiable action is reported as unverified and the cat says so.

It found two real bugs within minutes of being wired up. See the traps.

**Routing reads the conversation too.** Jev was classifying each sentence
alone, so "can you type about Elon Musk" ten seconds after opening Notepad
scored as a question ABOUT Elon Musk - which is exactly what it looks like,
read by itself. The router takes the same Memory as everything else and sends
recent turns with the sentence. Costs nothing measurable: 433ms with context
against 564ms without, ranges overlapping.

**Every path shares one memory.** `meow/memory.py` holds a short rolling
transcript and an actor per running task, read before each reply and written
after. The harness used to open a fresh LangGraph thread per turn, so it
remembered nothing at all and "now the other one" had nothing to resolve
against; it is one thread for the session now, trimmed to twelve messages.
Actors retire when a task is **dismissed**, not when it finishes - what the
task produced stays reachable, only its status line goes.

**A window means "walk away", not "multi-step".** A plan the user is watching
runs in the foreground with the thinking animation; only work they have left
running gets its own window. Deciding on sentence length got this wrong - a
four-step desktop job was handed a window to narrate what was already on
screen - so it is decided by what the job needs, and a hands-on verb (click,
type, press) means the user is watching whatever else the sentence says.

⚠ **The old per-task overlay panels are GONE.** They were layered
click-through windows, so there was nothing to click: results were visible and
unreachable, which is the worst of both. Background work lives in the chat
window now, reached by its tray icon.

⚠ **The planner's idea of what the cat can do has to be kept in step with the
harness.** Its prompt listed clicking, typing and opening applications and
never mentioned looking things up or making documents - tools added in 2.3 and
2.4 - so "find research on solar panel costs and put it in a spreadsheet", the
documented Phase 2 demo, could not be planned at all and came back as chat.
Adding a tool means updating `PLANNER_PROMPT`.

**Long work is handed over.** A plan becomes a background task with its own
small window: the cat says "i am on it" and goes back to listening. Say
"also ..." to queue something onto a running task, "close that" when it is
finished. Two run at once. Pause stops them all.

**A task can stop and ask, and only about what matters.** A handed-over job
used to decline anything needing a decision and report the gap at the end.
There is somewhere to put a question now: it goes into the task's own
conversation with yes/no buttons, the icon turns **amber** and stops spinning,
and the thread blocks. Nobody is interrupted - the question waits until it is
convenient, which is what consent needs in order to mean anything. Answer it
and the task carries on from exactly where it stopped, because it never
unwound. After four minutes it expires: silence is not consent.

⚠ **It asks ONLY about things that are hard to undo.** Delegating a job is
consent to the ordinary steps of doing it, so an unattended harness goes ahead
with opening an app, typing, clicking Save, ctrl+s. `Delete All Messages`,
`shift+delete` and `Send` stop and wait. A task that asks permission for every
step never finishes, and it takes back the walking-away that was the point of
handing it over.

⚠ **`TaskState.finished` lists its states rather than saying "not RUNNING".**
Written the other way, adding WAITING made every waiting task instantly count
as finished - icon retired, conversation closed, question discarded. For the
same reason `TaskRunner.running` filters on `state.working`.

**Plan state is durable.** `Documents/Meow/plans.db` via `SqliteSaver`. Every
step is a checkpoint, which made a plan resumable *within one process* - and a
plan interrupted by a crash was simply gone, at the moment its state was worth
the most. Verified across a real process restart, not inside one: an in-memory
saver passes every test that stays in a single process, which is how it went
unnoticed.

**An icon per running agent, pinned top right.** When Meow hands work over,
that agent gets its own icon down the right edge, below the window buttons;
clicking it opens the window straight onto that agent's conversation — what it is doing now and everything it has said. The icon goes
when the work finishes, with a notification. A tray that accumulates an icon
per job ever run is a tray people stop looking at; the conversation itself is
never lost.

⚠ **A handed-over task must never report that the user said no.** Its confirmer
declines without asking anybody — that is the whole point of
`declining_confirmer` — so "you said no, so i have stopped here" is a plain
untruth about something they never saw. `Planner(unattended=True)` says "that
needed your permission and you were not here, so i left it" instead.

⚠ **`GetAsyncKeyState`'s was-pressed bit accumulates while nothing is docked.**
The first poll after an agent appears covers however long the dock was empty,
so a click from minutes earlier gets consumed then — and if the pointer happens
to sit over the new icon, a window opens that nobody asked for. Seen live:
"opening the chat window on 19" in the instant the task was handed over. The
first read after the dock fills is discarded.

⚠ **Windows 11 hides new tray icons, per icon, forever.** The agent icons were
in the system tray first and it cannot work: a `QSystemTrayIcon` created fresh
per agent is one Windows has never seen, so it starts behind the chevron and
the user would have to un-hide every agent by hand. The diagnostics were
unambiguous that the code was right — `built icon ... visible=True
available=True` — and it was still not on screen. `meow/agentdock.py` draws
them beside the cat instead, where Meow owns the pixels.

**Three checks before believing anything works.** [docs/07-live-test.md](docs/07-live-test.md) is the third — thirteen spoken
scenarios covering what no script can: whether it heard you, whether the text
that arrived is the text you asked for, whether an icon is where you can
click it. Each step names the real bug it is watching for.

**Two automated checks.** `python scripts/smoke.py`
starts the real app and fails on a traceback; `python scripts/stress.py` throws
48 edge cases at every module — empty strings, 10,000 characters, Devanagari,
emoji, SQL, path traversal, reserved Windows filenames, eight threads at once,
a window killed mid-read. Both are verified to fail on real bugs.

⚠ **`ast.parse` and a printed banner are not a test.** A `dock.layout` call
with the wrong number of arguments shipped past both: the file parses, startup
prints its banner, and the crash is in the render loop a few frames later —
where `| head -9` truncated it out of view. Run `python scripts/smoke.py`,
which starts the real app, lets the loop turn, and fails on a traceback. It has
been verified to FAIL on that exact bug, because a check that cannot fail is
not a check.

⚠ **Windows' own pages are not executables.** There is no `Settings.exe`, so
the installed-application search cannot find Settings and WILL confidently find
something else containing the word — on this machine, **WSL Settings**, which
it opened twice while insisting it had not. `apps.SHELL_TARGETS` maps spoken
names to `ms-settings:` URIs and is checked BEFORE the installed search.

⚠ **Opening something already open is success, not failure.** It comes forward
instead of making a second window, so "no new window appeared" is true and
useless. `verify.opened` falls back to asking whether it is in front now.

⚠ **Producing a file ABOUT a topic is always a plan.** Find out, then write it —
two jobs. Jev called "gpu prices in india, put it in a spreadsheet" one action
purely because the word "research" was never said, so it ran in the foreground
and blocked the voice loop for half a minute with no icon to watch. Upgraded
structurally in `scripts/meow.py`, not left to the criteria alone.

⚠ **The agent icons are FIXED, not anchored to the cat.** They were stacked
above it first, and the cat moves — it follows the pointer and goes home — so
an icon was never twice in the same place and clicking one meant chasing it.

⚠ **Do not put them at the very top of the screen.** The top right corner of a
maximised window is its close, maximise and minimise buttons, and since these
overlays intercept clicks rather than passing them on, an icon there eats the
close click rather than merely covering it. `TOP_OFFSET = 96` clears a title
bar and a tab strip.

⚠ **The agent icons are the ONE overlay that is not click-through.** Everything
else passes clicks to the window underneath; an icon whose whole purpose is
being clicked cannot. `Overlay(click_through=False)`.

⚠ **A conversation still marked live belongs to a process that is gone.** A
crash, a Ctrl+C or a sleeping machine leaves them open forever, and the tray
then shows an agent icon for work that stopped days ago and can never finish.
The voice loop closes them at startup — the WINDOW must not, since it may start
while the loop is mid-task.

⚠ **Hold a `QSystemTrayIcon` and its `QMenu` as attributes.** One that goes out
of scope is garbage collected and silently vanishes from the tray, which looks
exactly like the agent having finished; a dropped menu leaves a right-click
that does nothing.

**The chat window.** A ChatGPT-shaped panel: conversations down the left,
painted bubbles on the right, search across everything ever said. It lives in
the tray and opens when clicked. Each conversation carries its own icon, so a
handed-over task is distinguishable from the voice session at a glance.

⚠ **It runs as its OWN PROCESS, and that is forced.** Qt wants the main thread
for its event loop and the cat's overlay already has it - a layered Win32
window at 60fps. They talk through the SQLite store, which also means the
window can crash or never be opened and the voice loop neither notices nor
cares.

⚠ **Qt rich text cannot draw a chat bubble.** `border-radius` does nothing and a
coloured `div` stretches the full viewport width, so an HTML transcript renders
as flat grey bars edge to edge. `meow/chat/bubbles.py` paints them with a
delegate instead: sized to their own text, the user's on the right.

**Stored, not just shown.** `Documents/Meow/conversations.db`. WAL so a reader
never blocks the writer, one connection per thread, busy timeout rather than a
retry loop. Measured: six threads writing 240 messages in 0.09s while a second
process polled throughout.

**Phase 2.6 done: a new capability is a markdown file.** A heading, a
`when:` line, a paragraph. Drop it in `recipes/` or `Documents/Meow/Recipes`
and the cat knows how to do the thing — no code, no release. That is invariant
1 made mechanical: when it cannot do something, write the paragraph.

Retrieval is **word overlap, model-free** — the target is CPU-only and this
runs before every act turn, so an embedding hop is wrong twice over. A recipe
must match on a **trigger** word to be considered at all; body words then raise
the score but never create a match on their own. Without that rule "what time
is it" scored 0.50 against the Settings recipe because "Time & language"
appears in a list of sidebar entries.

Verified it reaches the model with a recipe about an invented application:
without it, "click the export option in the toolbar" (confidently wrong); with
it, "tap the three-dot menu at the bottom left and select send out".

**Asking HOW gets instructions, not an action.** "how do i change my dns",
"where is bluetooth", "show me how to add a slide" route to SHOW, and in that
mode **every tool that changes anything refuses**. The cat reads the route out
and points at whatever step is on screen:

> *network and internet, then advanced network settings, then dns server
> assignment. edit is on screen now.*

⚠ **Enforced in the tools, not the prompt.** A prompt saying "do not click" is a
request; `Harness.guiding` makes `click_control`, `type_text`, `press_keys`,
`open_app` and `make_document` return a refusal instead of acting. The
difference between explaining a setting and changing it is not something to
leave to a model's judgement.

The route is mined from the page BODY, not the snippet — a snippet is two lines
chosen to match the query, and "Settings > Personalisation > Colours" is
something an author writes mid-paragraph. Where no arrow path exists, the
candidates are used in the order the page listed them, which for "Insert" then
"New Slide" is the route without the arrows.

**Research searches from several angles and says where it got things.** A
spoken sentence is not a query: "hey can you do a research on solar panel cost
and put it in the spreadsheet" carries politeness and an output format, neither
of which has anything to do with finding an answer. `meow/queries.py` strips
those with rules, then a small model writes two more queries from different
angles. Results are merged by agreement - a page that two queries both surface
outranks one that ranked first for a single phrasing - and capped at ONE per
domain, because four pages of the same site is one source wearing four hats.
Measured: 6 findings across 6 distinct domains, and a spoken citation.

⚠ **The query model NEVER sees a fetched page.** Rewriting happens before
anything is fetched, on the user's own words only. A page that could influence
the next search could walk the research anywhere it liked.

⚠ **Use a small model, not a new one.** Query rewriting is the lightest job
here, and `gpt-4.1-nano` does it in 1,467ms against 4o-mini's 1,606ms at a
fraction of the price. A gpt-5 *nano* is a reasoning model and spends a 90
token budget thinking, returning one query instead of three.

**Phase 2.5 done: it can point at settings nobody told it about.** Ask
"where is the bluetooth setting" and `meow/lookup.py` searches for what it is
*called*, then finds that exact name in the window in front. Measured against
real Windows Settings: dark mode -> `Personalization`, bluetooth ->
`Bluetooth & devices`, dns -> `Network & internet`.

**The containment rule is the whole design.** A web page is untrusted, so it is
never allowed to say what to DO - only what to LOOK FOR. Candidates are mined
as label-shaped strings, anything opening with an imperative verb is dropped
whole rather than trimmed, and a surviving name must match the tree
**exactly**. Then the cat POINTS. Pointing is `Risk.SAFE`, so the worst a
hostile page achieves is drawing attention to a button already on screen;
pressing it needs the user to say so, which goes through `meow/risk.py` where
the instruction comes from the person.

⚠ **Use `lookup.strict_match`, never `digest.find`, for a web-derived name.**
`find` degrades to substring and word-overlap matching, which is right for
speech and wrong here: the candidate "Settings" matched a VS Code GitLens
button whose 900-character name contains the word, and the cat announced it as
the setting. Under fuzzy matching, everything "exists" and the containment
claim is false.

**Phase 2.1–2.4 done.** `find research on solar panel costs and put it in a
spreadsheet` produces a real .xlsx with sources in ~30s. Research is a separate
component with search and fetch and **nothing else** — no files, no desktop, no
outbound — because it is the one that reads untrusted pages.

**Phase 2.1 and 2.2 detail.** Multi-step requests are broken into steps and run
one at a time by a LangGraph `StateGraph` — the plan *is* the state, each step
is a node visit, and the checkpointer makes it resumable. It says "step 2 of 4"
as it goes.

**Full ablation, six applications, 90 attempts** —
`python scripts/evaluate_suite.py --per-app 5 --strict`:

| strategy | hit rate | median miss | failure modes |
|---|---|---|---|
| **UIA** | **30/30** | **0 px** | none |
| vision | 0/30 | 1,032 px | 29 not found, 1 wrong element |
| vision-strict | 0/30 | 709 px | **30 wrong element** |

`vision-strict` is a control, not a strategy: coordinates only, no option to
decline. It exists because the conversational prompt emitted a coordinate tag
on **one of eight** tasks, and a baseline that answers one time in eight has
not been tested. Stripped down it answered 30 of 30 — and was wrong 30 of 30.
The baseline is not refusing to play; it lands 709 px from a control it can
see.

**UIA's 100% is close to tautological** — tasks are sampled from the digest and
UIA answers from that same digest. The vision number is the measurement; the
UIA number says only that nothing was dropped by the filter and every name
resolved. Say so in the writeup rather than letting a reviewer say it first.

At thirteen times the tokens the baseline is still 0/6, so it was not starved of
pixels. See [docs/04-evaluation.md](docs/04-evaluation.md), including the two
methodology bugs that produced plausible wrong numbers first.

**1.1 and 1.4 detail.** `meow/uia.py` returns the foreground
window as a ranked list of named, on-screen controls with exact coordinates, in
**268ms** — via native `FindAllBuildCache`, which is 23.5x faster than walking
the tree from Python and returns all of it rather than a truncated slice.

The digest is **~1,535 tokens against 2,833 for the cheapest screenshot**, so
the tree is cheaper *and* exact. Next: 1.2 the Grounding protocol, 1.5 the
harness that can actually click. See [docs/05-phases.md](docs/05-phases.md).

## Stack

| | |
|---|---|
| Language | Python 3.12 (installed) |
| Agent framework | LangGraph + `langchain.agents.create_agent` — **installed and in use**, see `meow/harness.py` |
| Model | **OpenAI `gpt-4o-mini`** — cost-constrained, see `meow/vision.py` |
| STT | **AssemblyAI v3 streaming** — `wss://streaming.assemblyai.com/v3/ws` |
| TTS | **ElevenLabs Flash v2.5** — `eleven_flash_v2_5` |
| Tracing | LangSmith — **every path is `ChatOpenAI` now, so a whole turn traces**, not only the part that used tools. On with `LANGSMITH_API_KEY`. |
| Router | Jev via `langchain-typesafe` — non-generative classifier |
| UIA | `uiautomation` (installed) |
| Win32 | `pywin32` (installed) |
| Connectors | `composio-langgraph` (Phase 4) |

Target machine is **CPU-only** — no local GPU inference. Not yet installed:
`ffmpeg` (needed for Phase 3), `uv`, `codex`, `aider`.

**Latency, measured rather than guessed.** A turn is: UIA digest 460ms,
Jev route 560ms warm, then two model rounds - decide which tool, then say what
happened. The model rounds are most of it and the rest was waiting.

- **The digest runs WHILE Jev routes.** Neither needs the other and each takes
  about half a second; run one after the other they were a second of silence.
  Measured: 1,079ms sequential against 729ms overlapped.
- **The harness STREAMS its reply.** It used `invoke`, so nothing at all came
  out until both rounds had finished - five seconds of silence, which reads as
  stuck rather than as thinking. Same model, same wording; only the moment it
  starts arriving. Verified streaming, not falling back: 21 chunks, first
  sentence at 3.8s and the second at 4.0s.
- **Waits POLL, they do not sleep.** Every wait was a flat sleep sized for the
  slowest case, so every case paid for the slowest one - Notepad is walkable
  993ms after launching and `open_app` slept 1,600ms regardless. `open notepad`
  went 5.9s to 5.0s.
- ⚠ **Do not skip the second model round to save time.** Tool output is written
  FOR THE MODEL: "Could not verify: ... Say you did it but could not claim it
  worked" is an instruction, not a sentence, and reading it out is nonsense.
  That round is also where a multi-tool turn decides its next tool, and where
  the agent signals it is done - so there is no way to know a turn was
  single-tool without having paid for it.

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
6. **Confirmation is targeted, not blanket.** Dangerous actions always ask,
   however plainly they were requested. Actions the user named themselves do
   not, because repeating their sentence back is how a prompt becomes
   furniture. Everything else asks. Judge the ACTION, not the sentence — "click
   that one" is harmless until it resolves to "Delete All Messages". See
   `meow/risk.py`. `run_powershell`, when it exists, is in the always-ask set.
7. **The overlay is excluded from capture** (`WDA_EXCLUDEFROMCAPTURE`) — or the
   cat appears in its own screenshots and confuses the model.
8. **Coordinates:** call `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`
   at startup, or clicks land off-target on secondary monitors.
9. **Restore anything you change system-wide.** `SetSystemCursor` replaces the
   pointer for every application until something puts it back, so a crash while
   installed leaves the user with a cat cursor and no explanation. Wire restore
   three ways - context manager, `atexit`, and SIGINT - as `meow/cat/cursor.py`
   and `spikes/wake_probe.py` both do.
10. **The user can always take back the mouse.** A pointer glide aborts the
   instant the cursor moves somewhere we did not put it. Never fight a user for
   control of their own machine.
11. **Never send a full-screen `detail=high` image.** 36,835 tokens against
   2,833 for low detail, which is *flat* regardless of resolution. Send nothing,
   or "unchanged", or low detail at full size, or a 512px high-detail crop. See
   `meow/vision.py`.
12. **Activation is tapped, never held.** The problem with Clicky's ctrl+option
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
- **Long text is pasted, not typed.** 211 characters took 10.6s at the
  only speed that does not corrupt them, and 0.51s through the clipboard -
  one keystroke, so there is no per-character timing left to get wrong.
  The user's clipboard is borrowed and PUT BACK; a paste that arrives
  empty falls through to typing, because some fields refuse it silently.
- **`SendInput` accepting keystrokes is not the application receiving
  them.** At 90 characters per second - the old default - Notepad got
  "hello rrom rrrrrrobe" for "hello from the probe"; at 60 it dropped a
  third of a pangram; at 30 it still mangled. **20 cps came back
  byte-identical** and is the default now. The call reports success at
  every speed, so nothing surfaces this except reading the text back.
- **Launching an application does not give it focus.** A freshly opened
  Notepad left focus on a button, on a group, and once on an entirely
  unrelated window - so text typed straight after a launch lands somewhere
  nobody predicted. `type_text` checks what has focus and refuses controls
  that cannot hold text.
- **The pointer drifts on its own.** This ThinkPad's TrackPoint moved the
  cursor a median of 0px but spiked to 57px while nothing touched it. Any
  "did the user grab the mouse?" check must test PERSISTENCE, not magnitude —
  drift never sustained past one sample, a hand trips three easily.
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
- **A permanent conversation thread carries stale context forward.** Each
  turn injects a UIA digest of the focused window; five turns in, the model
  holds five digests of five different windows whose element numbers point
  at trees rebuilt since. Tag per-turn blocks and drop the previous turn's.
  When trimming, cut only on a human message - an orphaned tool result,
  whose request went with the trim, is rejected outright by the API.
- **A permanent thread also returns every reply it ever made.** Yield only
  what is new, or the cat reads its history out loud before answering.
- **Transcripts arrive punctuated, and phrase matches are written without
  punctuation.** "close it" is not inside "close it.", so saying "close it"
  went to the agent, which closed Notepad, instead of dismissing the task.
  Strip punctuation before any spoken-phrase comparison.
- **The transcriber emits noise as sentences.** "Oh." was routed to plan and
  given its own background task and window. Drop short all-filler
  utterances before routing, not after.
- **AssemblyAI's default streaming model is MULTILINGUAL.** It hears
  accented English and renders it in the script of whichever language it
  settles on: "open notepad then type hi my name is srijaa" arrived as
  Devanagari transliteration - right words, wrong script - so the harness
  got a sentence it could not act on and would have typed Devanagari into
  Notepad. Nothing in the log reads as a transcription failure. Pin
  `speech_model=universal_streaming_english` and `language_detection=False`.
- **A word list cannot filter noise in a language you did not plan for.**
  A Hindi "haan" was routed to plan and given its own background window.
  The rule that survives translation is structural: one word is not an
  instruction, and three words are not a multi-step task.
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
  the ear. Never "simply" or "just". **Never end on a yes/no question** -
  enforced in code by `without_trailing_yes_no`, not left to the prompt,
  which says it twice and is ignored anyway. A bare "yes" carries no
  instruction, so whatever was half-planned gets done: one live reply ended
  "would you like to see it?" and the yes retyped a whole paragraph.
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
