# Meow

A voice-driven desktop companion for Windows. A cat lives on screen, hears you,
sees what you see, and **operates your machine with you** — narrating, and
asking before anything that matters.

Inspired by [Clicky](https://github.com/farzaa/clicky) (macOS, MIT). Not a port:
Clicky points at things and explains them; Meow does them.

Personal study project. Windows 11, Python 3.12, CPU only.

---

## The result

The question this project exists to answer: **should a desktop agent find
things by reading the screen, or by reading the accessibility tree?**

Vision is how most desktop agents work. It is also the assumption nobody had
measured on Windows, so this measures it — one system, one flag, three
strategies, the same tasks, the same frozen screen.

`meow evaluate --per-app 5 --strict` — six applications,
thirty tasks:

| strategy | hit rate | median miss | failure modes |
|---|---|---|---|
| **UIA** | **30/30 (100%)** | **0 px** | none |
| vision | 0/30 | 1,032 px | 29 not found, 1 wrong element |
| vision-strict | 0/30 | **709 px** | 30 wrong element |

Settings, File Explorer, Chrome, Notepad, Word, VS Code. Both regimes: sparse
windows 25/25 against 0/25, a dense Electron window 5/5 against 0/5.

**`vision-strict` is a control, not a strategy.** The first run's obvious
objection was that the baseline had not really been measured, because it kept
declining to answer. That objection was correct: the conversational prompt
produced a coordinate on **one of eight** tasks — the model said "it is over
here" and emitted nothing. A strategy that answers one time in eight has not
been tested. Stripped to coordinates only, with no option to decline, it
answered **30 of 30** — and was wrong 30 of 30, by a median of 709 px, which on
a 1280-wide screenshot is most of the way across the window.

So the baseline is not losing because it refuses to play.

**And the honest caveat, stated before a reviewer states it:** UIA's 100% is
close to tautological. Tasks are sampled from the accessibility digest and UIA
answers by looking a name up in that same digest, so a hit is nearly guaranteed
by construction. What it does establish is narrower — every sampled control
survived the digest filter and every name resolved unambiguously. **The vision
number is the measurement**, because it is scored against ground truth it did
not produce. A hand-labelled held-out set is the obvious next experiment and is
not done here.

Full method, including two methodology bugs that produced plausible wrong
numbers first, in [docs/04-evaluation.md](docs/04-evaluation.md).

The capability gap is larger than the number. A vision guess is a point; a UIA
hit is a *rectangle with a handle*, which can be invoked without the pointer
moving, on a window that is not even in front.

---

## What it does

Tap **Ctrl+M**, talk, tap **Pause** to stop everything.

- **Answers** — with a screenshot only when the question needs one
- **Points** — flies the pointer to what you named, wearing a cat cursor
- **Acts** — clicks, types, opens applications, presses shortcuts
- **Explains** — *"how do i change my dns"* reads the route out and points,
  and in that mode every tool that changes anything **refuses**
- **Researches** — searches from several angles, cites its sources, writes a
  real `.xlsx` / `.docx` / `.pptx`
- **Hands work over** — long jobs get their own icon and their own chat
  conversation, and can stop to ask you a question without interrupting you

Everything is kept. Conversations live in `Documents/Meow/conversations.db` and
are browsable in a chat window that opens from the tray.

---

## Running it

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env        # then paste your keys in
meow doctor

meow
```

Keys needed: **OpenAI** (the model), **AssemblyAI** (streaming speech to text),
**ElevenLabs** (speech). Optional: **LangSmith** for tracing, **Typesafe** for
the Jev router — without it, routing falls back to keywords.

Windows only, and deliberately. The whole project rests on Windows UI
Automation and Win32 layered windows; there is no cross-platform path and none
is planned.

### Checking it works

```
meow stress     # 52 edge cases across every module
meow smoke      # starts the real app, fails on any traceback
```

Both are verified to fail on real bugs, because a check that cannot fail is not
a check. There is a third — [docs/07-live-test.md](docs/07-live-test.md), thirteen
spoken scenarios covering what no script can: whether it heard you, whether the
text that arrived is the text you asked for, whether an icon is somewhere you
can click it.

---

## How it is put together

```
Jev (reflex: route · risk · complexity — non-generative)
 │
 ├──▶ RESEARCH   search + fetch only. No files. No desktop. No send.
 ├──▶ HARNESS    LangGraph agent · UIA grounding · confirmation gate
 └──▶ EXPLAINER  manim pipeline (not built)
```

The model never produces a coordinate. It reads a list of named controls and
asks for one **by name**, which resolves against the accessibility tree to an
exact rectangle — so it cannot produce a wrong coordinate, only an unknown
name.

**Safety is structural, not prompted.** No component gets all three of private
data, untrusted content, and a way to send things out. Research reads web pages
and holds no tool that could act on one. The harness has the desktop and no
outbound channel. Guide mode is enforced by the tools refusing, not by asking
the prompt nicely — a prompt saying "do not click" is a request, and a tool
that will not click is a guarantee.

Search-augmented pointing is the place those two meet, so the rule there is
sharper: **a web page is never allowed to say what to DO, only what to LOOK
FOR.** Candidates are mined as label-shaped strings, anything opening with an
imperative verb is dropped whole rather than trimmed, a surviving name must
match the tree exactly, and the output is a *point* — which changes nothing.

Details in [docs/03-safety.md](docs/03-safety.md).

---

## Where it is

Phases 0, 1 and 2 are complete: the spine, grounding and the harness, the
planner and knowledge work. Not built: the Manim explainer, connectors, and
delegation to a coding CLI.

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
project has walked into — Electron nesting UIA content thirty levels deep,
`WH_KEYBOARD_LL` dying under Chromium focus, `SendInput` corrupting text above
20 characters per second while reporting success, AssemblyAI transcribing
accented English into the wrong script, Windows 11 hiding new tray icons
per-icon forever.

---

## What is not a contribution

Being clear about this is part of the point.

- **The cat** is not a technical contribution. It stays because it makes the
  demo memorable and character genuinely lowers the intimidation barrier — but
  it is not a result.
- **The explainer**, when built, largely reproduces
  [TheoremExplainAgent](https://arxiv.org/html/2502.19400). Cite it.
- **Voice, overlay, capture** are engineering, not research.

Two honestly-measured claims are worth more than six asserted ones.

---

## Licence

MIT. See [LICENSE](LICENSE).

Clicky is MIT and by [@farzaa](https://github.com/farzaa); this project reuses
none of its code, and owes it the idea and several design choices that turned
out to be right for reasons documented in
[docs/06-prior-art.md](docs/06-prior-art.md).
