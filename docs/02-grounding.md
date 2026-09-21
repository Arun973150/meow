# Grounding

> **How does the agent know where a thing is on screen?**

This is the project's primary technical claim. It is also the part most likely
to need revision — see "The hole" below, and run `spikes/uia_probe.py` before
committing to anything here.

---

## Two strategies

**Vision grounding** (what Clicky does): send a screenshot, ask the model for
`(x, y)`. Clicky's entire pointing system is a regex over a text tag the model
appends to its reply:

```
[POINT:x,y:label:screenN]     or     [POINT:none]
```

The model is told the image dimensions are the coordinate space, origin
top-left. That is the whole mechanism. It works, and it guesses.

**Accessibility-tree grounding** (what Meow does): query Windows UI Automation
for an element by name and role, get back an exact bounding rectangle and an
`Invoke()` handle. No guessing. Works on occluded and background windows,
because it never needed to see pixels in the first place.

**Occluded is not minimized.** A window buried behind three others reads fine.
A *minimized* window reports a zero-size rect and cannot be walked at all —
measured, see below. Anything the agent needs from a minimized app has to be
restored first, which is a visible action and therefore a confirmable one.

---

## Why this is Windows-specific

macOS's accessibility API is meaningfully weaker and more restricted than
Windows UIA. This is not a feature Clicky simply chose not to build — it is
something the platform makes harder. That is what makes this a contribution
rather than a port.

It is also the same API that NVDA and JAWS are built on, which is the honest
link to the accessibility framing in [00-scope.md](00-scope.md).

---

## The hole that was not there

**The original claim in this section was wrong, and the way it was wrong is
worth keeping on the record.**

It read: *Chromium-based apps expose only an application→frame skeleton until
launched with `--force-renderer-accessibility`.* That is what the first spike
appeared to confirm:

```
Code.exe       SKELETAL     6 actionable      depth 12   (truncated)
chrome.exe     RICH       161 actionable      depth 10
```

Two days went into explaining that asymmetry — Electron's broken screen-reader
detection, Chromium's lazy tree construction, the `SPI_SETSCREENREADER` flag
that NVDA and JAWS set. A second spike was written to test the flag.

**None of it was real.** The walker had `MAX_DEPTH = 12`. Electron nests window
content roughly **30 levels** below the frame. The walk was stopping a third of
the way in and reporting what it found as the whole tree.

### Measured — same machine, cap raised

```
PROCESS        VERDICT   TOTAL   NAMED   ACTION   RECTS   DEPTH    WALK
Code.exe       RICH       1638     861      819    1626      39   1.18s
chrome.exe     RICH        248      69      153      87      10   0.27s
explorer.exe   RICH         77      76       74      77       3   0.20s
```

VS Code exposes **819 actionable elements with bounding rectangles**, not 6.

Two confounds were ruled out by A/B, not by argument:

| Suspected cause | Test | Result |
|---|---|---|
| `SPI_SETSCREENREADER` | set system-wide, re-probe, restore | **no change** — 6 → 6 at depth 12 |
| `editor.accessibilitySupport` | `"on"` vs `"off"`, same depth | **no change** — RICH either way at depth 40 |

Neither mattered. The depth cap was the entire finding.

### What this actually means

**UIA grounding works on Electron.** The regime that was supposed to be the
thesis's biggest risk does not exist on this machine. Chrome, VS Code and
Explorer all come back RICH, which makes the grounding claim stronger than when
it was written, not weaker.

Three regimes are still the right model — `EMPTY` is real for games and canvas
surfaces, and other Electron builds may still differ — but **SKELETAL is now
unproven.** It should not be asserted again without a run at full depth.

### The lesson worth more than the result

A truncated walk had been classified as a regime. `SKELETAL` and "we stopped
early" are indistinguishable from the outside, and the code reported the first
when it meant the second.

`Probe.verdict` now returns **`TRUNCATED`** and refuses to classify at all when
a cap was hit. This is the general form of the bug and it will recur elsewhere —
timeouts, element caps, filtered walks. Anywhere the system reports what it
found, it has to report what it *stopped* looking at.

The same class of error cost the wake experiment its control condition: Chrome
was minimized during that run, so it appeared in neither BEFORE nor AFTER.
Minimized windows report a zero-size rect and cannot be walked. Both spikes now
print a `NOT WALKED` block instead of dropping them silently.

### The real problem, relocated

Availability was never the constraint. **Selection is.**

819 actionable elements against a ~150-element digest budget means roughly
**80% of the tree has to be discarded before the model sees any of it** — and
discarding the wrong 80% fails exactly like an empty tree. That is the hard
problem, and it is a better one than the one this document started with.

Walk cost is the other half. A full-depth VS Code walk is **~1.0–1.2s**, and
Chrome varied from 0.27s to 1.12s across runs on identical windows. Whole-window
walks do not fit a sub-second voice loop. Scoped subtree queries are mandatory,
not an optimization.

## The reframe

The honest research question is better than the one we started with:

> *When does accessibility-tree grounding work, when does it fail, and what
> should an agent do about it?*

That gives a real contribution rather than a benchmark number:

1. **Element selection under a budget** — which ~150 of 819 does the model need?
   This is now the hard problem, and the one with the most headroom.
2. **Regime detection** — classify the focused window at runtime, and **refuse
   to classify a truncated walk**. `TRUNCATED` is not a regime, it is a bug
   report about the walker.
3. **Strategy switching** — UIA where it works, vision where it does not.
   `EMPTY` is still real for games and canvas surfaces.
4. **Per-app-class reporting** — one number hides the finding; regimes show it.

Wake techniques were the third item here and have been **removed**. Both
candidates were tested and neither did anything; see "The hole that was not
there". If a genuinely skeletal Electron build turns up at full depth, this
comes back.

`HybridGrounding` stops being a fallback and becomes the actual result.

---

## What a screenshot costs

Measured on gpt-4o-mini, this account, a 1280x800 desktop. These numbers decide
the vision strategy, and one of them is counter-intuitive:

| configuration | image tokens |
|---|---|
| text only | 9 |
| `detail=low` 512px | **2,833** |
| `detail=low` 768px | **2,833** |
| `detail=low` 1280px | **2,833** |
| `detail=high` 512px | 8,500 |
| `detail=high` 768px | 14,167 |
| `detail=high` 1280px | **36,835** |

**At low detail the cost is flat.** 512px and 1280px charge exactly the same.
Downscaling to save money achieves nothing there, so send the largest
low-detail image available - the extra pixels are free.

**At high detail it scales hard.** A full screen is thirteen times a low-detail
one. When small text genuinely has to be read, the answer is to *crop*: a 512px
crop at high detail is 8,500 tokens against 36,835 for the whole desktop, and
the crop is usually more relevant anyway.

`meow/vision.py` applies this as a policy, cheapest first: send nothing, then
send "the screen has not changed", then low detail at full size, then a
high-detail crop. Full-screen high detail is not on the list.

On a $5 budget that is the difference between roughly 880 turns and 8,000.

**This is also an argument for the UIA digest over pixels.** A 150-element tree
digest is around 1,500 tokens - cheaper than the cheapest screenshot, and exact
rather than guessed. The cost constraint and the thesis point the same way.

---

## Built - what the tree actually costs

Phase 1.1 and 1.4, measured on this machine against VS Code.

**Ask UIA, do not walk it.** Reading name, role and rectangle node by node from
Python costs ~1.2ms per element, because each is a cross-process COM call. The
native `FindAllBuildCache` asks for only the control types that matter and
pre-fetches their properties in one round trip:

| method | result | time |
|---|---|---|
| Python breadth-first walk | 5000 elements, 728 actionable | 6170 ms |
| `FindAllBuildCache` | 756 actionable, **complete** | **262 ms** |

23.5x faster, and the speed is the lesser point: the walk had to be truncated,
so it returned an arbitrary slice. The native call returns the whole tree.

**The selection problem was mostly an artefact of how we counted.** The spike
found 819 actionable elements in VS Code, and that number framed 1.4 as "which
150 of 819?". But that count includes elements scrolled out of view, sitting in
collapsed menus, or carrying no name at all - none of which a user can refer to
or a model can act on. Filter to **on-screen AND named**:

```
762 actionable found  ->  120 usable  ->  120 sent
```

Under the 150 budget, with no ranking needed. Ranking still exists for windows
where it is not, and the scoring function is deliberately written as named,
separable weights because every one of them is a guess until 04-evaluation.md
measures it.

**And it is cheaper than a screenshot.** The digest for that window is ~1,535
tokens against 2,833 for the cheapest possible image - so the accessibility tree
is both more accurate *and* less expensive than vision. The two arguments for
the thesis point the same way.

Full digest, end to end including process lookup: **268 ms**.

---

## The interface

Build grounding as a **swappable strategy from the first commit.** This is what
makes the ablation in [04-evaluation.md](04-evaluation.md) possible without
maintaining two codebases.

```python
class Grounding(Protocol):
    def locate(self, target: str, ctx: ScreenContext) -> Rect | None: ...

class VisionGrounding:    # screenshot → model guesses (x, y)   [the Clicky baseline]
class UIAGrounding:       # accessibility tree → exact rect
class HybridGrounding:    # regime detection → UIA or vision
```

One system, one flag, three modes. Retrofitting this later means rebuilding the
action layer.

---

## The UIA digest

A full desktop tree can be thousands of elements and take seconds to walk.
Uncapped, it becomes both the worst latency sink and the worst token sink in the
system.

Rules:

- **visible and interactable only** — drop decorative panes and groups
- **cap at ~150 elements**
- **focused window first**, then siblings
- name + role + rect, nothing else
- prompt-cache it — it is large and repeated

The spike measures walk time per app. Any app over ~1s cannot be walked whole
inside a sub-second voice loop, and needs a scoped subtree query instead.

**Measured, full depth:**

| App | Elements | Actionable | Walk |
|---|---|---|---|
| VS Code | 1638 | 819 | **1.18s** |
| Chrome | 248 | 153 | 0.27s — but **1.12s** on an identical window in another run |
| Explorer | 77 | 74 | 0.20s |

Two things follow, and both are binding:

- **VS Code cannot be walked whole.** 1.18s exceeds the entire voice budget on
  its own. Scoped subtree queries from the focused element outward are the only
  workable shape.
- **Walk time is not stable.** Chrome varied 4× across runs on the same window
  with the same element count. Any latency budget has to be built on the worst
  case, not the median, and the digest needs a hard deadline with a partial
  result rather than an unbounded wait.

The earlier note here claimed 0.14–0.40s and concluded the latency risk had not
materialized. That was measured against trees truncated at depth 12 — it was
timing an incomplete walk.

---

## Coordinate pipeline

Three coordinate spaces have to reconcile, and getting this wrong means every
click misses:

```
screenshot pixels  →  physical pixels  →  virtual desktop coords
```

Non-negotiable: call `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` at
startup. Without it, `SendInput`, `GetCursorPos` and `GetSystemMetrics` are
DPI-virtualized on any monitor whose scale differs from primary, and clicks land
off-target on secondary screens.

Also note the virtual desktop has **negative coordinates** for monitors placed
left of or above primary.

For reference, Clicky's capture settings: max edge 1280px, JPEG quality 0.8,
cursor's screen sorted first, own windows excluded from capture.

Anthropic's computer-use tool expects coordinates in screenshot pixel space and
supports up to a 2576px long edge on current models — but recommends staying at
or under 1920×1080 for performance.
