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
