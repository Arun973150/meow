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

**Phases 0, 1, 2 and 4 are COMPLETE.** Phase 3 (the Manim
explainer) and Phase 5 (delegation) are not started. The map:

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
meow/chat/window.py         the chat window - PySide6, its OWN PROCESS
meow/chat/bubbles.py        painted bubbles - Qt rich text cannot do them
meow/chat/icons.py          icons drawn with QPainter, not shipped as files
meow/chat/launcher.py       starting the window, and recording into it
recipes/                    the shipped recipes; the user's go in Documents/Meow
meow/connectors/            PHASE 4 - READER reads, SENDER sends, you decide
meow/cat/cursor.py          cat_cursor.png as the system cursor, restored
meow/voice/microphone.py    16kHz mono PCM16, bounded queue, RMS level
meow/voice/stt.py           Transcriber protocol + AssemblyAI v3 streaming
meow/voice/tts.py           Speaker protocol + ElevenLabs eleven_flash_v2_5
meow/cli.py                 one command: meow · doctor · stress · smoke
meow/app/loop.py            THE WHOLE LOOP - routes, answers, points, presses
meow/desktop/               uia · actions · grounding · pointing · verify ·
                            lookup · apps · vision - the thesis lives here
meow/agent/                 harness · planner · router · jev · risk · memory ·
                            mind · evaluation - the parts that decide
meow/work/                  tasks · taskwindow · agentdock · conversations
meow/knowledge/             research · queries · recipes · documents
meow/storage/paths.py       WHERE THINGS GO - user files vs app data
meow/config.py              .env keys; never logs a value
meow/console.py             UTF-8 stdout - cp1252 cannot print what STT returns
meow/panic.py               the stop button - top level, so it stays obvious
meow/language/phrases.py    noise, agreement, what a sentence is asking for
meow/language/routing.py    the structural corrections to a route
meow/tools/desktop.py       click · point · type · press · open · switch
meow/tools/connectors.py    mail · calendar · tasks · docs · READ and DRAFT
meow/tools/knowledge.py     look things up, and how to do them
meow/tools/workspace.py     documents, and what a task already made
meow/tools/support.py       measured constants, below harness and tools both
meow/tools/record.py        ToolRun - shared, so neither imports the other
meow/testing/stress.py      64 edge cases across every module
meow/testing/smoke.py       runs the REAL app and fails on any traceback
meow/diagnostics/           keys · connectors · the ablation
tests/                      pytest - language, dictation, and the invariants
scripts/*_demo.py           the demos: overlay, cat, companion, listen
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

**PHASE 0 IS COMPLETE.** `meow` - tap Ctrl+M, talk, and the
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

**Phase 1 is complete and reachable by voice.** `meow` — tap
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
that a field received them. `meow/desktop/verify.py` snapshots the desktop either
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

**Every path shares one memory.** `meow/agent/memory.py` holds a short rolling
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

**Plan state is durable.** `%LOCALAPPDATA%/Meow/plans.db` via `SqliteSaver`. Every
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
available=True` — and it was still not on screen. `meow/work/agentdock.py` draws
them beside the cat instead, where Meow owns the pixels.

**Three checks before believing anything works.** [docs/07-live-test.md](docs/07-live-test.md) is the third — thirteen spoken
scenarios covering what no script can: whether it heard you, whether the text
that arrived is the text you asked for, whether an icon is where you can
click it. Each step names the real bug it is watching for.

**Two automated checks.** `meow smoke`
starts the real app and fails on a traceback; `meow stress` throws
64 edge cases at every module — empty strings, 10,000 characters, Devanagari,
emoji, SQL, path traversal, reserved Windows filenames, eight threads at once,
a window killed mid-read. Both are verified to fail on real bugs.

⚠ **`ast.parse` and a printed banner are not a test.** A `dock.layout` call
with the wrong number of arguments shipped past both: the file parses, startup
prints its banner, and the crash is in the render loop a few frames later —
where `| head -9` truncated it out of view. Run `meow smoke`,
which starts the real app, lets the loop turn, and fails on a traceback. It has
been verified to FAIL on that exact bug, because a check that cannot fail is
not a check.

⚠ **"minimise" and "Minimize" are the same instruction.** Windows labels its
buttons in American spelling and people say the British one, so the risk gate
never matched them and asked permission to minimise a window it had just been
told to minimise. `risk._spelling` folds -ise/-ize, -isation/-ization and
-yse/-yze before comparing.

⚠ **`win+m` minimises EVERY window.** Asked to minimise one, the model reached
for it — a whole-desktop action from a request about a single window, with the
right button sitting in the digest. `press_keys` sends those back to
`click_control` rather than asking, because "press win+m?" cannot be answered
usefully by someone who does not know it applies to everything.

⚠ **A Store app is an AppUserModelID, not a file.** `App Paths` and the
Start-menu `.lnk` walk between them found 211 applications and could not see
Camera, Calculator, Photos, Clock, Xbox or WhatsApp, because a UWP app has no
executable to find. `apps.start_apps()` reads `Get-StartApps` through
PowerShell — 241 on this machine — and `launch` opens them with
`explorer.exe shell:AppsFolder\<AppID>`, since `os.startfile` cannot resolve a
virtual folder. It costs about a second, so it is consulted alongside the cheap
sources rather than instead of them.

⚠ **Match by STRENGTH, not by source.** Asking the installed list first meant
"photos" found **Photoshop** — photoshop starts with photos, and a prefix hit
from the first source beat an exact hit from the second. Exact beats prefix
beats substring, whichever list it came from.

⚠ **Windows' own pages are not executables.** There is no `Settings.exe`, so
the installed-application search cannot find Settings and WILL confidently find
something else containing the word — on this machine, **WSL Settings**, which
it opened twice while insisting it had not. `apps.SHELL_TARGETS` maps spoken
names to `ms-settings:` URIs and is checked BEFORE the installed search. The
same is true of Camera, Photos, Calculator, Clock and the Store — "there is no
camera application installed" was said about a machine that ships with one.

⚠ **Opening something already open is success, not failure.** It comes forward
instead of making a second window, so "no new window appeared" is true and
useless. `verify.opened` falls back to asking whether it is in front now.

⚠ **Producing a file ABOUT a topic is always a plan.** Find out, then write it —
two jobs. Jev called "gpu prices in india, put it in a spreadsheet" one action
purely because the word "research" was never said, so it ran in the foreground
and blocked the voice loop for half a minute with no icon to watch. Upgraded
structurally in `meow/app/loop.py`, not left to the criteria alone.

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

⚠ **The chat window is DETACHED, so it outlives a crash - and every run
spawned another one.** `stop()` only runs on a clean quit, so a Ctrl+C, a
crash or a killed smoke test leaves the window up, and the next launch starts a
second. **30 orphaned Qt processes were live on this machine**, each holding a
tray icon for a session that ended days ago and showing a conversation nothing
writes to any more. The launcher asks the OS whether a window is already
running before spawning - asked of Windows rather than tracked in a pid file,
since a file written by a killed process claims a window that is not there.
Costs ~580ms, once, at startup only.

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

**Stored, not just shown.** `%LOCALAPPDATA%/Meow/conversations.db`. WAL so a reader
never blocks the writer, one connection per thread, busy timeout rather than a
retry loop. Measured: six threads writing 240 messages in 0.09s while a second
process polled throughout.

**PHASE 4 IS COMPLETE. An email has actually been sent and read back.**
Composed, refused before approval, approved, delivered, and refused a second
time on the same approval - then VERIFIED BY READING THE MAILBOX rather than
by trusting what the API returned, because this project's own rule is that a
call returning success is not the thing having happened.

The mutation attack was re-run against the live path: the payload proxy
raises, and forcing past it with `object.__setattr__` is still refused at send
by the fingerprint. Nothing reached the attacker address.

`GMAIL_SEND_EMAIL` takes `recipient_email`, `subject`, `body` - checked against
the schema BEFORE sending, after the captions bug proved that guessing
argument names costs a live run.

**The reader/sender split.** Mail, calendar, Slack and
YouTube, with the trifecta enforced structurally. The READER reads anything and
holds no tool that sends; the SENDER takes an approved draft by **id** and
holds no tool that reads. Between them is a person looking at the exact
recipient and the exact body.

**Access is asked for when it is needed, not configured beforehand.** Say
"what's in my inbox" with no Gmail connected and the cat says *"i need access
to your gmail, opening the login now"*, opens Google's login, waits, and
carries on. A companion should ask to use your mail when you ask it to read
your mail, the way an application asks for the microphone when you press
record — not in a setup step you complete without knowing which parts you will
use.

Composio-managed OAuth, so there is no Google app to register and no client
secret on this machine. The flow the API actually wants:

    POST /auth_configs                     once per toolkit, reusable
    POST /connected_accounts/link          -> a redirect_url
    the browser                            the user logs in
    GET  /connected_accounts/{id}          poll until ACTIVE

⚠ **`POST /connected_accounts` is refused for managed OAuth.** It returns a 400
naming `/connected_accounts/link` as the replacement — worth reading the error
rather than assuming the obvious endpoint.

⚠ **Retry the login only on "not connected".** Opening a login tab in answer to
a rate limit or a bad argument is answering the wrong question loudly.

⚠ **A connected account is reachable only through a TOOL, so the sentence has
to reach the harness.** "What's in my inbox" is shaped exactly like a
question, Jev routed it to ANSWER, and the answer path holds no tools at all —
so the cat replied out of its own knowledge and talked about the screenshot it
had been handed: *"i'm not looking at your screen right now, tell me what
emails you see in your inbox."* Every connector worked by script and none of
them worked by voice, which is the only way anyone uses this. `CONNECTOR_WORDS`
in `meow/app/loop.py` upgrades ANSWER to ACT, structurally, for the same reason
the artefact upgrade is structural: a question about your own inbox is one no
model can answer from its own knowledge, so there is nothing for a classifier
to weigh. SHOW is left alone — that is someone asking how to do it themselves.

**The user never has an API key, and never should.** The Composio key belongs
to whoever BUILT Meow — one key for the application — and the Google login
belongs to whoever is running it. Those are different things held by different
people, and the only question a user is ever asked is the login. Shipping this
means the key moves behind a proxy like the AssemblyAI token does; it is in
`.env` today because this runs on one machine.

⚠ **`user_id` was the literal string `"default"`, for everybody.** Composio
separates one person's connected accounts from another's by `user_id` and
nothing else, so a shared default plus one developer key is one inbox between
every install — person B says "what's in my inbox" and reads person A's mail.
`composio.this_install()` writes a random id to `%LOCALAPPDATA%/Meow/install-id`
once and reuses it. Verified by the connection disappearing: YouTube connected
under the old default is invisible to the new identity, which is the isolation
being real rather than nominal.

⚠ **`YOUTUBE_LOAD_CAPTIONS` takes a CAPTION TRACK id, not a video id.** Both
are called `id`. Passing the video id returns "Following fields are missing:
{'id'}", which reads as an absent argument rather than a wrong one, and sent
this looking in the wrong place. It is two calls:
`YOUTUBE_LIST_CAPTION_TRACK` first, then load a track from it. The argument
spellings differ per tool — `videoId` there, `id` for `YOUTUBE_VIDEO_DETAILS`
— so read each schema from `/api/v3/tools/{slug}` rather than assuming.

⚠ **Composio's shared OAuth app has a shared Google quota, and it is
exhaustible.** `YOUTUBE_VIDEO_DETAILS` returned a 403 `quotaExceeded` on a
freshly connected account that had made one call. Nothing is wrong with the
connection when this happens. Real use needs your own Google OAuth client in
the auth config, which is also what stops one noisy install from spending
everyone's quota.

⚠ **Connecting a toolkit grants its WRITE scopes too.** The YouTube toolkit
holds `UPLOAD_VIDEO`, `UPDATE_VIDEO`, `UPDATE_THUMBNAIL` and
`SUBSCRIBE_CHANNEL` alongside the reads. The containment is that `Reader`
names only read tools — there is no path from a spoken sentence to a tool slug
— so the grant is broad and the reachable surface is not.

**Reading is a harness tool; sending is not.** `read_mail`,
`read_message`, `my_agenda`, `explain_video` and `draft_reply` are in the
harness — it already holds private data and untrusted content, and gains no
outbound channel from any of them. `draft_reply` composes and puts a draft in
the outbox; the voice loop sends, after the user says so.

⚠ **The sender could deliver three kinds of draft and only ONE could be
made.** `compose_reply` was the only draft factory in the project, so
`calendar_event` and `slack_message` were dead code in `_deliver` - and asked
to put an interview in the calendar, the cat fell through to the desktop and
said "i cannot see your calendar right now, please open the calendar
application", about an account that was connected the whole time. A delivery
path nothing can reach is worse than a missing one: it reads as finished.

**The line between a tool and a draft is whether the DESTINATION is an
argument.** Mail takes a recipient, Slack takes a channel, a calendar invite
takes attendees - each of those arguments is an exfiltration channel, and the
draft-and-approve path exists to put a person in front of it. A to-do has no
recipient: it goes to the user's own default list and nowhere else, so nothing
a hostile page said can choose a destination. `add_task` is therefore a plain
tool and `draft_event` is not.

⚠ **A spoken date is the same problem as a spoken address.** "25th of
September" carries no year, and `reader.spoken_datetime` returns None rather
than a guess for anything it cannot pin down - an event on the wrong day is
worse than no event, because nobody finds out until the day. A date already
past rolls forward a year, and the tool makes the model read the whole
resolved day and time back.

⚠ **There is NO send tool in the harness, and there must never be one.** The
harness holds the screen, which is private data and untrusted content both;
one send tool closes the trifecta in a single move. 32 tools, none of them
send-shaped, and `meow/testing/stress.py` checks that.

**Approval is explicit.** Saying "send it" sends the newest waiting draft, and
that is checked BEFORE routing so it cannot be re-interpreted by a model. A
bare "yes" does not send — the one irreversible thing needs a sentence that
could only mean it. The chat window shows the draft with the exact recipient
and body, and its buttons say **send** and **discard** rather than yes and no.

⚠ **Nobody can dictate an email address, and spelling it out is WORSE.**
Normalising the transcription was not enough. "at the rate" came back as
"around" and produced `gaurav@032gmail.com` - syntactically valid, so it passed
every check, and only a human reading it back caught it. Asked to spell it, the
transcriber returned `go w d a r u n`, `It's go w d a then a`, and a bare `G.`
was DROPPED ENTIRELY by the short-utterance noise filter. Three minutes, no
address. This is not fixable at the transcription layer, so the address is
never dictated: `find_contact` resolves a spoken NAME against
`Documents/Meow/contacts.txt` first and the user's own mail second, and the
tool tells the model to draft immediately rather than ask which to use.

⚠ **`Documents/Meow/contacts.txt` is how an address gets in at all.** One
`name = address` per line. A lookup cannot invent an address for somebody who
has never written to you, and that is exactly the case that matters for a first
email. Typing it once beats saying it correctly never - the same bargain as a
recipe.

⚠ **Gmail's `from:` TOKENISES.** `from:arun` does not match
`arunspotifyxo@gmail.com`, and a half-heard first name is what a microphone
delivers. A plain search is the fallback - and because that returns whole
messages, any address the name does not appear in is DROPPED rather than ranked
low. Offering a stranger's address as a weak match is how mail reaches the
wrong person.

⚠ **A spoken email address never arrives as a valid email address.** The
transcriber hears "gowda arun zero three two at gmail dot com" and writes
`Gauda Arun 032 gmail.com` - spaces through the middle, the @ gone, sometimes
"at" and "dot" left as words. Passed through, the draft held an impossible
address and nothing surfaced until SEND: "invalid email format passed". The
loop that followed is the real damage - the cat asked for the address "without
spaces", which the user cannot say, because the spaces come from the
transcriber. Four attempts, no mail. `drafts.spoken_email` normalises at the
point the address is first seen, returns "" rather than a guess, and the tool
makes the model read the address back character by character before anything
is sent.

⚠ **The sender takes a draft ID, not arguments.** `send(draft_id)`, never
`send(to, subject, body)`. A sender that accepts arguments can be called with
arguments assembled from anything, including the email it just read; one that
accepts an id can only send something already approved.

⚠ **`frozen=True` does NOT freeze a dict inside the dataclass.** It stops
`draft.payload = ...` and does nothing about `draft.payload["to"] = ...`. This
was live and the attack test proved it: a draft approved to a colleague was
mutated afterwards and delivered to `attacker@example.com` — the user approved
one message and a different one went out. The payload is deep-copied into a
`MappingProxyType` now, and approval is bound to a **fingerprint** of the
contents that is re-checked at send, so a future mutable path fails closed
rather than delivering quietly.

⚠ **Composio's SDK cannot be installed.** It requires `openai>=3` and
`langchain-openai` requires `openai<3` — installing it breaks the model client
for the harness, the planner, the answer path and the query rewriter. Use the
REST API over `httpx`, which is already a dependency.

⚠ **The shipped recipes stopped loading when `recipes.py` MOVED.**
`shipped_folder()` was `parent.parent / "recipes"`, right while the module sat
at `meow/recipes.py` and wrong the moment it became `meow/knowledge/recipes.py`
- so all six vanished, silently, because a shelf with nothing on it looks
exactly like a request that matched nothing. They live in `meow/recipes/` now,
INSIDE the package, which is also the only place a wheel would carry them; the
path and the `package-data` entry have to agree.

⚠ **A `when:` list WRAPS, and only the first line was being read.**
`new-document.md` lost "new file", "new note", "new sheet", "new workbook",
"new slide" and "new deck" from the day it was written. The list now runs to
the first BLANK line - the rule a person would guess from looking at the file,
which is the only rule worth having in a format whose promise is that you can
write one without reading documentation.

⚠ **A title written as a sentence donates useless triggers.** The title
counts as a trigger, which is right until the title is "start something new in
an application" - it donated "start" and "something", and both match anything.
"start recording" and "film something" tied against it on exactly those two
words. Generic words are FILLER now.

⚠ **Only the literal word "camera" ever opened the camera.** "take a photo"
worked and "capture" did not, because nothing maps the words to the
application - the MODEL had to make that leap, and did so inconsistently. That
is what a recipe is for, and `meow/recipes/camera-photos-video.md` lists the
vocabulary: photo, picture, selfie, snap, capture, webcam, video, record,
recording, film, shoot. A bare "video" otherwise matches the **VideoLAN
website** in the installed-application search.

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

⚠ **A new tab changes NOTHING a snapshot could see.** Same window title,
same process, same focused role, same focused name — "Text editor" before and
after — so opening a new note was done correctly and honestly reported as
unverifiable, which is the worst combination. `Snapshot.focused_id` carries
UIA's runtime id for the focused control, 3ms to read, and it is the only
signal that notices.

⚠ **A multi-word recipe trigger must match as a PHRASE.** Split into loose
words, `when: new window` fired the new-document recipe on "minimise this
window" — because *window* alone was enough, and *window* alone means nothing.
All of a phrase's words must be present; single-word triggers still match
singly.

⚠ **A SHOW turn must POINT, not describe.** Refusing the acting tools was
only half of it: asked "where is the file menu", the model answered out of its
own memory and pointed at nothing. A remembered layout is precisely what this
project measured as wrong — the tree knows and the model does not. `guiding`
injects `GUIDE_REMINDER`, which says a tool MUST be used: `point_at_control`
for anything in the list, `find_how_to` if it is not there, and "it is not on
this screen" when neither finds it.

⚠ **The model has coordinates and no sense of them.** It pointed correctly at
Minimize in the top right and told the user it was "at the bottom right corner"
— pointer in the right place, sentence sending them to the wrong one, which is
worse than saying nothing. `point_at_control` returns `where_on_screen(target)`
so there is a true answer to repeat instead of one to invent.

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
of which has anything to do with finding an answer. `meow/knowledge/queries.py` strips
those with rules, then a small model writes two more queries from different
angles. Results are merged by agreement - a page that two queries both surface
outranks one that ranked first for a single phrasing - and capped at ONE per
domain, because four pages of the same site is one source wearing four hats.
Measured: 6 findings across 6 distinct domains, and a spoken citation.

⚠ **The query model NEVER sees a fetched page.** Rewriting happens before
anything is fetched, on the user's own words only. A page that could influence
the next search could walk the research anywhere it liked.

⚠ **A research turn gets NO control list.** Asked to research GPU prices with
Chrome focused, the cat reached into Chrome - the turn injects a digest of the
foreground window, and a browser's digest is full of plausible things to press
next to a question about prices. Worse after any Chrome work earlier in the
session, because the router's own act criterion says a sentence continuing what
Meow just did is an act. `harness.wants_the_web` withholds the digest entirely
rather than asking the model to ignore it, the same way `guiding` refuses in
the tools rather than in the prompt. Naming the desktop yourself opts back in:
"search for it in chrome" and "click the address bar and search" still get the
screen.

⚠ **`stream_mode="messages"` streams EVERY model in the graph, including one a
tool builds for itself.** `look_up` builds a query rewriter, so its three
generated search queries were streamed to the user and SPOKEN - "GPU price
trends India 2024, Nvidia GPU cost India 2024..." read aloud before the answer.
Skip chunks whose `langgraph_node` is `tools`: a model running inside a tool is
not the cat talking, whatever it produces.

⚠ **Use a small model, not a new one.** Query rewriting is the lightest job
here, and `gpt-4.1-nano` does it in 1,467ms against 4o-mini's 1,606ms at a
fraction of the price. A gpt-5 *nano* is a reasoning model and spends a 90
token budget thinking, returning one query instead of three.

**Phase 2.5 done: it can point at settings nobody told it about.** Ask
"where is the bluetooth setting" and `meow/desktop/lookup.py` searches for what it is
*called*, then finds that exact name in the window in front. Measured against
real Windows Settings: dark mode -> `Personalization`, bluetooth ->
`Bluetooth & devices`, dns -> `Network & internet`.

**The containment rule is the whole design.** A web page is untrusted, so it is
never allowed to say what to DO - only what to LOOK FOR. Candidates are mined
as label-shaped strings, anything opening with an imperative verb is dropped
whole rather than trimmed, and a surviving name must match the tree
**exactly**. Then the cat POINTS. Pointing is `Risk.SAFE`, so the worst a
hostile page achieves is drawing attention to a button already on screen;
pressing it needs the user to say so, which goes through `meow/agent/risk.py` where
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
`meow evaluate --per-app 5 --strict`:

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

**1.1 and 1.4 detail.** `meow/desktop/uia.py` returns the foreground
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
| Agent framework | LangGraph + `langchain.agents.create_agent` — **installed and in use**, see `meow/agent/harness.py` |
| Model | **OpenAI `gpt-4o-mini`** — cost-constrained, see `meow/desktop/vision.py` |
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
 ├──▶ HARNESS    32 tools · 2 prompts · reactive | deliberate(planner)
 └──▶ EXPLAINER  manim pipeline, fixed shape
```

## Layout

Installable, with one command and subcommands:

```
pip install -e .
meow                 the companion
meow doctor          what is installed, which keys are set
meow connectors      which services are connected, and connect one
meow stress          64 edge cases across every module
meow smoke           start the real app, fail on a traceback
pytest               71 fast checks - no Windows, no keys, no network
```

**A capability is a module, not a diff.** `Harness.__init__` defined all
thirty tools inline, so it was 1,108 lines and every new capability - however
unrelated - edited the same function. They are `meow/tools/` now, one module
per concern, each exposing `build(harness)`. Adding one is adding a file and a
line in `ORDER`.

⚠ **Two things sit BELOW both harness and tools, to break a cycle.** The tools
need `ToolRun` and the measured constants, and the harness imports the tools -
so those live in `meow/tools/record.py` and `meow/tools/support.py`. A shared
type belongs under both, never inside one.

⚠ **`meow/platform/` shadows the stdlib `platform` module** if `meow/` ever
lands on `sys.path`. Moving the loop into the package left a `sys.path.insert`
pointing at `meow/`, and zstandard then died on
`platform.python_implementation()` - a traceback three libraries away from the
cause. There is no `sys.path` juggling left; keep it that way.

⚠ **`RegisterHotKey` is exclusive and a killed process does not release it
instantly.** Two smoke runs back to back meant the second could not take
ctrl+m and exited, which the test reported as "the app died" - pointing at the
app rather than at the previous run. It waits for the key now and names the
real cause. Every intermittent smoke failure in one long session traced to a
single stray background process.

## Where things are kept

Three places, because three different things were in one folder and they have
different owners. `meow/storage/paths.py` decides all of them.

```
Documents/Meow          what the USER opens - .docx .xlsx, their recipes,
                        contacts.txt. Explorer has to be able to reach it.
%LOCALAPPDATA%/Meow     what the APP owns - conversations.db, plans.db,
                        install-id, window.log. Never synced, never roamed.
```

> Measured on this machine: the whole store is **15,688 characters across 294
> messages**. Any plan for hosting it is a sync and backup problem, not a
> scale one.

⚠ **Documents is NOT `%USERPROFILE%\Documents`.** Windows' Known Folder
Move points it at OneDrive once backup is on, and on this machine it does: the
shell says `C:\Users\ADMIN\OneDrive\Documents` while the code wrote to
`C:\Users\ADMIN\Documents`. Both exist, so nothing ever failed - the app just
saved spreadsheets into a Documents folder Explorer does not show, after
announcing that everything lands in Documents/Meow. Ask the shell:
`SHGetKnownFolderPath(FOLDERID_Documents)`.

⚠ **A SQLite database must never live in a synced folder.** WAL is a second
file that has to stay consistent with the first, and a sync service copies them
independently and on its own schedule. The old path avoided this BY ACCIDENT,
by writing to the un-synced twin - so fixing Documents correctly would have
introduced the corruption the bug was hiding. Databases go to LOCALAPPDATA,
which Windows guarantees is neither roamed nor synced.

⚠ **A migration must never merge two databases.** Two conversation stores with
overlapping autoincrement ids do not combine, so anything already at the
destination is left alone and the old copy kept. And it must report what it
could not move: the chat window is its own process holding `conversations.db`,
so the file that matters most is the one most likely to be stuck, and two
databases with no hint which is live is the worst outcome of a move.

⚠ **Directories are not an afterthought in a migration.** Skipping them left
the user's own recipes behind - the one feature whose whole point is that it
belongs to them. Merged file by file, never folder-over-folder.

**That split is the seam for hosting any of this later.** `documents()` is
files and belongs to file sync; `app_data()` is state, and state is what a
server would hold. Adding that does not have to move anybody's spreadsheets.

⚠ **Syncing forces an identity decision.** `install-id` is per-MACHINE on
purpose, after a shared `"default"` let one person's Gmail be read by every
install. The moment conversations sync, identity has to become per-PERSON or
the same human on two machines is two users and re-authorises everything -
which means real accounts, a bigger step than storage.

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
   `meow/agent/risk.py`. `run_powershell`, when it exists, is in the always-ask set.
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
   `meow/desktop/vision.py`.
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
