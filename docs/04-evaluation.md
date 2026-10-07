# Evaluation

For a study project the demo is not the deliverable — the **measured claim** is.
This document exists so the measurement gets designed before the code, not
bolted on at the end.

---

## Claim 1 — grounding

> Accessibility-tree grounding outperforms vision grounding on desktop tasks,
> **in the app regimes where the tree is exposed** — and regime detection with
> strategy switching outperforms either alone.

The qualifier is not hedging. It is the finding. See
[02-grounding.md](02-grounding.md).

### Design: single-variable ablation

One system, one flag, three grounding strategies. Same model, same tasks, same
prompts. Only `Grounding` changes.

| Metric | Measures | Expected shape |
|---|---|---|
| **task success rate** | did it hit the right element? | UIA wins where the target survives the digest |
| **tokens per task** | UIA digest vs repeated screenshots | UIA lower where it works |
| **latency per action** | tree query vs vision round-trip | UIA lower — but a full VS Code walk is 1.18s, so measure the scoped query, not the whole walk |
| **digest recall** | was the correct element still in the ~150 sent to the model? | **the one that explains the others** |

Reporting one aggregate number **hides the result**. Report per regime:

```
                      RICH          DENSE         EMPTY
                  (Explorer,     (VS Code,      (games,
                   Word, Settings) Chrome)       canvas)
                   ~75 elements   800+ elements  no tree
  vision            __ %            __ %          __ %
  uia               __ %            __ %          __ %
  hybrid            __ %            __ %          __ %
```

That table is the project's central result.

**SKELETAL has been replaced by DENSE**, and the change is the finding. The
spike expected Chromium apps to expose nothing; measured at full depth they
expose *819 actionable elements* — the opposite failure. The axis that matters
is not "is the tree there" but "does the right element survive a 150-element
budget", so the regimes are now split by tree size. See
[02-grounding.md](02-grounding.md).

If a genuinely skeletal app turns up, the row comes back. None has so far.

### First run - measured (pilot, one application)

Superseded by the full suite above; kept because the two methodology
bugs it turned up are the useful part.

`python scripts/evaluate.py --tasks 6`, VS Code, DENSE regime, one screen frozen
for every task and every strategy.

| strategy | hit rate | median miss | median ms | cost / 6 tasks |
|---|---|---|---|---|
| **UIA** | **6/6 (100%)** | **0 px** | 0 ms lookup, 268 ms digest | ~1,535 tokens |
| vision, `detail=low` | 0/6 | 908–1,840 px | 2,323 ms | 17,000 tokens, $0.0031 |
| vision, `detail=high` | 0/6 | 91 px on its one attempt | 2,411 ms | 221,000 tokens, $0.0337 |

The high-detail row exists to answer the obvious objection: was the baseline
just starved of pixels? At **thirteen times the tokens** it is still 0/6. What
improves is precision when it does answer - 91px out instead of 1,840 - not
whether it answers. On five of six tasks it declined to point at all, because
the targets are small UI chrome and it genuinely could not resolve them.

**Ground truth is free, and that is the design.** UIA returns the exact
rectangle of every control, from the operating system that drew it, so the tree
labels the data the vision system is scored against. No hand-labelling, and the
suite runs against whatever is on screen rather than a fixed set of screenshots
that slowly stops resembling anything real. A vision answer counts as correct
when its point falls **inside** the true rectangle - the criterion that matters,
because that is where a click lands.

This does assume UIA is right about its own widgets, which is safe where a tree
exists and meaningless where one does not. EMPTY-regime windows are refused
rather than silently scored against nothing.

### Two methodology bugs, both found by running it

Worth recording, because both produced plausible numbers that were wrong.

**The window moved during the experiment.** Ground truth was captured once while
each lookup re-read the live screen, and a vision call takes ~2.4s. Over six
tasks that is a running application scrolling out from underneath. UIA scored
3/6 - not because it failed, but because it was asked about controls that had
moved. Both strategies are now pinned to one captured screen.

**The optimisation sabotaged the baseline.** The frozen screenshot is
byte-identical every task, so the unchanged-screen deduplicator in `vision.py`
skipped the image on five of six tasks. The baseline was being asked to locate
controls with no picture attached, and scoring 0/6 for entirely the wrong
reason. `ScreenContext.forget()` now disables it for experiments.

A handicapped baseline proves nothing, which is the whole reason Clicky's method
is reproduced faithfully rather than approximated.

### Full suite - measured

`python scripts/evaluate_suite.py --per-app 5 --strict`, six applications,
five tasks each, every application frozen before it is measured.

| strategy | hit rate | median miss | median ms | failure modes |
|---|---|---|---|---|
| **UIA** | **30/30 (100%)** | **0 px** | 0 (lookup) | none |
| vision | 0/30 (0%) | 1,032 px | 1,825 | 29 not found, 1 wrong element |
| vision-strict | 0/30 (0%) | 709 px | 1,810 | **30 wrong element** |

Applications: Settings (RICH, 27 of 53 controls), File Explorer, Chrome,
Notepad (RICH, 22 of 23), Word (RICH, 47 of 50), VS Code (DENSE, 116 of 541).
By regime: DENSE 5/5 UIA against 0/5 both vision conditions; RICH 25/25
against 0/25. Cost: 60 vision calls, ~194k input tokens, $0.0296.

### The control condition, and why it exists

The obvious objection to the pilot was that the baseline had not really been
measured, because it kept declining to answer - five of six tasks came back
"not found", which is a model refusing rather than a model failing to see.

Measured directly on VS Code: the conversational prompt emitted a coordinate
tag on **one of eight** tasks. The model said "it is over here" and produced no
tag at all. A strategy that answers one time in eight has not been tested.

`StrictVisionGrounding` strips everything else away - no personality, no spoken
sentence, no option to decline, reply with a tag or nothing, guess if unsure.
That took tag emission from 1/8 to **8/8**, and across the full suite it
answered **30 out of 30** times.

It was wrong all thirty, by a median of 709 px.

That is the result worth reporting. The baseline is not losing because it
refuses to play. It answers every time, and lands 709 px from a control it can
see, which on a 1280-wide screenshot is most of the way across the window.

### The limitation a reviewer will raise first

**UIA scoring 100% is close to tautological, and the honest reading is that
vision's 0% is the measurement.**

Tasks are sampled from the UIA digest, and `UIAGrounding` answers by looking a
name up in that same digest. A hit is nearly guaranteed by construction. What
the 30/30 does establish is narrower than it looks, and still worth having:
every sampled control survived the digest filter, and every name resolved
unambiguously. `DROPPED_BY_DIGEST` is a real failure mode that could have
fired thirty times and fired zero, which is a statement about the filter rather
than about the platform.

The vision number carries the weight, because it is scored against ground truth
it did not produce. An OS-drawn rectangle is an independent label for a vision
system in a way it is not for the tree that emitted it.

A stronger design would label a held-out set by hand and score both strategies
against it. **That experiment has since been run — see below.**

---

## The held-out set: 44 targets, described and marked by hand

`meow label` records a target when a person points at something on their own
screen, presses F8, and types what they would **call** it — "the button that
closes this window", not "Close". Two things then hold that did not above: the
words are the user's rather than the digest's, and the point is where a human
said it was rather than where the tree said. The screenshot and the digest are
saved with each label, so the set replays offline as often as you like.

`meow evaluate --labelled`

| | computer-use | UIA |
|---|---|---|
| Chrome (a chess board) | 13/17 | 0/17 |
| Photoshop | 6/10 | 3/10 |
| DaVinci Resolve | 2/3 | 0/3 |
| Premiere Pro | 1/6 | 0/6 |
| Illustrator | 1/8 | 0/8 |
| **all** | **23/44** | **3/44** |

The tautology is gone and the answer inverts. **Only 5 of the 44 were in the
UIA digest at all — and UIA still answered 18 times**, matching descriptions
onto unrelated controls up to 1,288 px away.

That is the finding, and it is sharper than "the tree wins". On a canvas the
tree does not fail to answer; it answers **wrongly and fast**, which is the
worse failure, because nothing in the reply suggests a guess. So the two cases
have to be told apart before either is trusted — `uia.only_chrome` is what
does it, and `lookup.already_on_screen` is the strict tier that keeps word
overlap away from a canvas.

**`computer-use` is not the same baseline as `vision`.** Declaring OpenAI's
`computer` tool activates coordinate-specific training; asking a
conversational model for a `[POINT:x,y]` tag does not. So the 0/30 above should
be read narrowly — "gpt-4o-mini emitting a text tag cannot point" — which is
true, rather than "vision cannot point", which is not. Say that in the writeup
rather than letting a reviewer say it.

### Three improvements that did not work

All measured against the same 44, which is the only reason they are worth
recording:

```
crop and zoom the guess          23/44 -> 23/44     at double the latency
name the application in front    23/44 -> 23/44     identical per-app
hand it the screenshot up front     no change       it takes its own anyway
```

Zooming is the *published* training-free method here, reported elsewhere at
+13.4% on ScreenSpot-Pro. It was built with an agreement gate — a second look
is believed when it SHARPENS and distrusted when it DISAGREES, since without
the gate it was worse than net zero in both directions — and verified on the 20
targets it was not tuned against. It reproduced as nothing.

The useful part is negative: **Illustrator, the worst application in the set,
did not move for any of the three**, and six of its eight failures are the
model *declining* rather than missing. So "it cannot see Illustrator's panels"
is the finding, and resolution, prompt wording and application context are all
answers to a different question. That rules out a family of ideas cheaply.

### One that did: it does not miss, it misses confidently

The failure mode matters more than the rate. One look answers 36 of 44 and is
right 25 — so eleven marks a session land on something the user never asked
about. And the model is not stable: two runs of the same 44 flipped their
verdict on **six**, with one target 6 px out on one run and 545 px out on the
next. That instability is a usable signal.

Two looks at the same screenshot, in parallel, answering only where they agree.
Scored twice, because one pass is a sample rather than a property:

```
                        marks drawn      right        WRONG
one look                  36 / 37       25 / 21      11 / 16
two looks, must agree     29 / 29       24 / 21       5 /  8
```

The absolute hit rate wanders by four between runs; the shape reproduces
exactly. Both times it drew eight fewer marks and lost at most one correct one,
so what it declines is almost entirely what it was getting wrong. Precision
69% → 83% on one run, 57% → 72% on the other. Three were rescued, which was not
the aim: the midpoint of two agreeing looks beats either.

**The threshold is 8 px, not the zoom gate's 64.** Those compare different
things — the refinement weighs a whole-screen guess against a point derived
from an enlarged crop, so "better located" has to be allowed for, while two
looks see the identical picture at the identical size. When either look was
right the two landed 0–6 px apart in 24 of 26 cases:

```
8px     draws 29, 24 right,  5 wrong      <- the same hits, two fewer misses
64px    draws 31, 24 right,  7 wrong
128px   draws 34, 25 right,  9 wrong
```

**It costs no wall clock.** The two calls do not depend on each other, so they
go out together: 7.2 s median for the pair against 7.8 s for one, and 7,342 ms
against 6,939 ms through `meow evaluate`. It costs tokens, not seconds.

Two reporting traps worth naming before a reader hits them. The **median miss
goes up**, 113 px to 151 px, because the gate removes marks and the statistic
is then over a smaller, harder set — read `answered` alongside `hits`. And a
disagreement is **not** "it is not on your screen": the thing is there and only
which one is missing, so it is reported as uncertainty with a request to circle
it, which is the one signal measured to change this problem at all.

### Task suite

~30 tasks across 6 apps, balanced across regimes. Each task is one unambiguous
target with a checkable outcome.

| App | Regime | Example tasks |
|---|---|---|
| File Explorer | RICH | sort by date · new folder · open Downloads |
| Word | RICH | bold selection · insert table · save as PDF |
| Settings | RICH | open Bluetooth · toggle dark mode · change resolution |
| Chrome | DENSE (153 actionable) | new tab · bookmark page · open history |
| VS Code | DENSE (819 actionable) | open command palette · toggle sidebar · new file |
| VLC / Spotify | mixed | play · next track · fullscreen |

Record per attempt: strategy, success, wall-clock, tokens in/out, and the
failure mode when it fails (wrong element / **element dropped by the digest** /
no element / wrong coordinates / timeout).

"Dropped by the digest" is a separate mode from "no element" and has to be
counted separately — it is the difference between the platform failing and our
filter failing, and only one of those is interesting.

**The failure-mode breakdown is as publishable as the success rate.**

---

## Claim 2 — routing latency

> Running a non-generative classifier on interim transcripts makes intent
> routing effectively free.

| | Serial | Parallel |
|---|---|---|
| STT finalize | 250ms | 250ms |
| Route | +80ms | **0ms** |
| Screen capture | +200ms | **0ms** |
| LLM first token | +450ms | 450ms |
| TTS first audio | +120ms | 120ms |
| **First syllable** | **~1100ms** | **~820ms** |

Measure ms-to-first-syllable over ~50 utterances, both configurations. Report
median and p95 — p95 is where the real experience lives.

Cheap to run, roughly two days of work, and the technique is uncommon enough to
be worth writing up.

---

## What is not a contribution

Be honest about this in the writeup:

- **The cat** is not a technical contribution. Keep it — it is what makes the
  demo memorable, and character genuinely lowers the intimidation barrier for
  anxious and older users — but do not claim it as a result.
- **The Manim explainer** largely reproduces
  [TheoremExplainAgent](https://arxiv.org/html/2502.19400) (93.8% success on 240
  theorems). Cite it. The contribution there, if any, is the interaction model:
  narrated in real time by a companion rather than rendered as a standalone
  video.
- **Voice, overlay, capture** are engineering, not research.

A project with two honestly-measured claims is stronger than one with six
asserted ones.

---

## Baseline to actually build

The ablation needs `VisionGrounding` implemented properly, not as a strawman. It
should be a fair reproduction of Clicky's approach — the `[POINT:x,y]` tag over
a 1280px screenshot — or the comparison proves nothing.

Budget time for it. This is the step most often skipped, and skipping it
invalidates the whole evaluation.
