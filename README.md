# Meow

A voice-driven desktop companion for Windows. A cat lives on screen, hears you,
sees what you see, and **operates your machine with you** — narrating, teaching,
and asking before anything that matters.

Inspired by [Clicky](https://github.com/farzaa/clicky) (macOS, MIT). Not a port:
Clicky points at things and explains them; Meow does them, and teaches you to.

Personal study project. Windows 11, Python 3.12, CPU only.

---

## The result

The question this project exists to answer: **should a desktop agent find
things by reading the screen, or by reading the accessibility tree?**

It measured both, twice, and the answer turned out to depend on the window.

### Where the tree can see, it wins outright

`meow evaluate --per-app 5 --strict` — six applications, thirty tasks, one
frozen screen, three strategies:

| strategy | hit rate | median miss | failure modes |
|---|---|---|---|
| **UIA** | **30/30** | **0 px** | none |
| vision | 0/30 | 1,032 px | 29 not found, 1 wrong element |
| vision-strict | 0/30 | **709 px** | 30 wrong element |

Settings, File Explorer, Chrome, Notepad, Word, VS Code.

**`vision-strict` is a control, not a strategy.** The first run's obvious
objection was that the baseline had never really been tested, because it kept
declining to answer — the conversational prompt produced a coordinate on **one
of eight** tasks. Stripped to coordinates only, with no option to decline, it
answered **30 of 30** and was wrong 30 of 30, by a median of 709 px. So the
baseline is not losing because it refuses to play.

**The honest caveat, stated before a reviewer states it:** UIA's 100% is close
to tautological. Tasks are sampled from the accessibility digest and UIA
answers by looking a name up in that same digest. **The vision number is the
measurement.**

### Where the tree is blind, it is worse than useless

Which is why the second experiment exists: **44 targets, described and marked
by hand**, in applications that draw their own interfaces.

| | computer-use | UIA |
|---|---|---|
| Chrome (a chess board) | 13/17 | 0/17 |
| Photoshop | 6/10 | 3/10 |
| DaVinci Resolve | 2/3 | 0/3 |
| Premiere Pro | 1/6 | 0/6 |
| Illustrator | 1/8 | 0/8 |
| **all** | **23/44** | **3/44** |

Only 5 of the 44 were in the UIA digest at all — **and UIA still answered 18
times**, matching descriptions onto unrelated controls up to 1,288 px away.
That is the finding that matters. On a canvas the tree does not fail to
answer; it answers **wrongly and fast**, which is the worse failure, because
nothing in the reply suggests a guess.

So the thesis is not "the tree wins". It is: *the tree is exact where it can
see, blind and confident where it cannot, and the two cases must be told apart
before either is trusted.* `uia.only_chrome` is what tells them apart — a
window showing nothing but Minimize, Maximize and Close is EMPTY, not RICH.

### And one negative result, kept because it cost something

Zooming is the published training-free way to improve GUI grounding. Built,
measured on the same 44 targets, with an agreement gate, and verified on the
20 it was not tuned against:

```
                one pass    two passes
combined (44)     23/44       23/44       7s -> 16s
```

No gain, at double the latency, so it is off by default. The useful part is
that **Illustrator — the worst application in the set — did not move at all**,
which rules out resolution as its problem and a whole family of ideas with it.

Full method, including the methodology bugs that produced plausible wrong
numbers first, in [docs/04-evaluation.md](docs/04-evaluation.md).

---

## What it does

Tap **Ctrl+M**, talk. Tap **Pause** to stop everything.

**Talks and acts**

- **Answers** — with a screenshot only when the question needs one
- **Points** — flies the pointer to what you named, wearing a cat cursor
- **Acts** — clicks, types, opens applications, presses shortcuts. The model
  never produces a coordinate; it asks for a control **by name**
- **Checks** — snapshots the desktop either side of an action and reports what
  actually changed. A verdict is yes, no, **or could not tell**, and the third
  matters most

**Teaches**

- **One step at a time, watching.** *"I don't know how to change dark mode"* is
  somebody telling you they cannot hold three steps and find them at once. So
  it says one, waits, and watches the screen — and says the next when you have
  done it
- **Procedures, not just menu routes.** *"Teach me how to animate a bouncing
  ball"* in Blender becomes four steps paced one at a time, from what the model
  knows. The screen goes with every turn so it can see what you actually did
- **Draws on your screen** — target rings, numbered badges, arrows, curves, a
  spotlight that dims everything else. Hand-drawn rather than plotted
- **And you can draw back.** Tap **Ctrl+Shift+M**, circle something, then ask
  about it

**Reaches further**

- **Explains** — *"how do i change my dns"* reads the route out and points, and
  in that mode every tool that changes anything **refuses**
- **Researches** — several angles, merged by agreement, cited, written to a
  real `.xlsx` / `.docx` / `.pptx`
- **Catches you up** — mail, calendar and the web at once, then an editor picks
  the handful worth saying
- **Repeats itself** — *"check my inbox every couple of hours"*
- **Hands work over** — long jobs get their own icon and chat conversation, and
  can stop to ask a question without interrupting you

**A new capability is a markdown file.** A heading, a `when:` line, a
paragraph, dropped in `Documents/Meow/Recipes`. Add `app: blender` and it
becomes a *skill* — offered whenever that window is in front, withheld
everywhere else.

---

## Running it

```
python -m venv .venv
.venv\Scripts\activate
pip install -e .

cp .env.example .env        # then paste your keys in
meow doctor

meow
```

Keys needed: **OpenAI** (the models), **AssemblyAI** (streaming speech to
text), **ElevenLabs** (speech). Optional: **LangSmith** for tracing,
**Composio** for the connectors. Without OpenAI, routing falls back to keywords
and the rest stops.

Windows only, and deliberately. The whole project rests on Windows UI
Automation and Win32 layered windows; there is no cross-platform path and none
is planned.

### Checking it works

```
pytest               # 573 fast checks - no Windows, no keys, no network
meow stress          # 70 edge cases against real UIA and real overlays
meow smoke           # starts the real app, fails on any traceback
meow routing         # replays 56 sentences that were spoken out loud
```

Every one of them is verified to fail on a real bug, because **a check that
cannot fail is not a check** — `meow smoke` once passed an app that never
started, and the fix was to make it wait for the app to say it was ready.

`tests/test_heard.py` is the corpus that matters: every transcript in it was
spoken at this machine and printed by the running app, untidied. The suites
kept passing while live runs kept failing, because the suites were written
against sentences a developer types and the microphone produces something
else — dropped words, homophones, run-together words, swallowed starts.

And [docs/07-live-test.md](docs/07-live-test.md) is the third check: twenty
spoken scenarios covering what no script can, each naming the real bug it is
watching for.

---

## How it is put together

```
ROUTER  gpt-4.1-nano, strict JSON schema - route · needs_screen · risky
 │
 ├──▶ RESEARCH   search + fetch only. No files. No desktop. No send.
 ├──▶ HARNESS    LangGraph agent · 40 tools · UIA grounding · confirmation gate
 └──▶ EXPLAINER  manim pipeline (not built)
```

Routing runs on **interim transcripts**, while you are still talking, so its
1.2 s lands off the critical path. `meow routing` scores it at 56/56 on real
speech, with four structural corrections carrying what the classifier cannot
know — a question about your own inbox is not something a model can answer from
its own knowledge, so that is decided in code rather than weighed.

**Safety is structural, not prompted.** No component gets all three of private
data, untrusted content, and a way to send things out. Research reads web pages
and holds no tool that could act on one. The harness has the desktop and **no
send tool, and there must never be one** — the stress suite checks. Guide mode
is enforced by the tools refusing, not by asking the prompt nicely: a prompt
saying "do not click" is a request, and a tool that will not click is a
guarantee.

Mail is the sharpest case. The **reader** reads anything and holds nothing that
sends; the **sender** takes an approved draft by *id* and holds nothing that
reads. Between them is a person looking at the exact recipient and the exact
body. A sender that accepted arguments could be called with arguments assembled
from the email it just read.

Search-augmented pointing is where untrusted text meets the desktop, so the
rule there is sharper still: **a web page is never allowed to say what to DO,
only what to LOOK FOR.** Anything opening with an imperative verb is dropped
whole, a surviving name must match the tree exactly, and the output is a
*point* — which changes nothing.

Details in [docs/03-safety.md](docs/03-safety.md).

---

## Where it is

Phases 0, 1, 2 and 4 are complete: the spine, grounding and the harness, the
planner and knowledge work, and the connectors. Not built: the Manim explainer
(Phase 3) and delegation to a coding CLI (Phase 5).

| Doc | For |
|---|---|
| [docs/00-scope.md](docs/00-scope.md) | goals, non-goals, open questions |
| [docs/01-architecture.md](docs/01-architecture.md) | harness, specialists, primitives |
| [docs/02-grounding.md](docs/02-grounding.md) | UIA vs vision — the core thesis |
| [docs/03-safety.md](docs/03-safety.md) | the lethal trifecta, and the tool split |
| [docs/04-evaluation.md](docs/04-evaluation.md) | the ablation, in full |
| [docs/05-phases.md](docs/05-phases.md) | build order |
| [docs/06-prior-art.md](docs/06-prior-art.md) | verified facts about Clicky and platform APIs |
| [docs/07-live-test.md](docs/07-live-test.md) | the spoken test |

[AGENTS.md](AGENTS.md) carries the invariants and every platform trap this
project has walked into. A sample, each of which cost real time:

- Electron nests UIA content ~30 levels deep; a depth cap of 12 looked exactly
  like a platform limitation and was a bug in the walker
- `WH_KEYBOARD_LL` stops firing while a Chromium window has focus
- `SendInput` corrupts text above 20 characters per second **while reporting
  success**
- AssemblyAI's default streaming model transcribes accented English into the
  wrong script
- Windows 11 hides new tray icons, per icon, forever
- `frozen=True` does not freeze a dict inside the dataclass — an approved draft
  was mutated afterwards and delivered to a different address
- Two turns on one LangGraph thread corrupt it, and every later turn 400s

---

## What is not a contribution

Being clear about this is part of the point.

- **The cat** is not a technical contribution. It stays because character
  genuinely lowers the intimidation barrier — but it is not a result.
- **The explainer**, when built, largely reproduces
  [TheoremExplainAgent](https://arxiv.org/html/2502.19400). Cite it.
- **Voice, overlay, capture** are engineering, not research.
- **The zoom refinement** is a reproduction attempt that did not reproduce
  here, and is reported as one.

Two honestly-measured claims are worth more than six asserted ones.

---

## Licence

MIT. See [LICENSE](LICENSE).

Clicky is MIT and by [@farzaa](https://github.com/farzaa); this project reuses
none of its code, and owes it the idea and several design choices that turned
out to be right for reasons documented in
[docs/06-prior-art.md](docs/06-prior-art.md).
