"""Does the router still send sentences to the right place?

    meow routing

Every sentence here was said out loud at some point, and most of them went
somewhere useless once. The project's own standard is that routing is measured
rather than assumed - "18/19 on real sentences" was the figure before the
classifier changed, and swapping the classifier is exactly the moment that
number stops being evidence of anything.

What is scored is the route the LOOP acts on, which is the model's answer with
`language.routing.correct` applied. The raw answer is reported next to it so
you can see which corrections are load-bearing: a rule that never fires is
dead weight, and one that fires constantly is a prompt that should be fixed
instead.

Two fields are scored narrowly, and the narrowness is the point.

`needs_screen` is only ever READ on the answer path - `loop.py` attaches a
screenshot there and nowhere else - so it is scored only on cases that stay
ANSWER. Scoring it on a sentence that gets promoted to ACT would be measuring
a number nothing downstream looks at, which is how a suite ends up green about
something that does not work. On the act path the harness decides for itself,
with `look_at_screen`.

`risky` is read for every acting turn, as one more reason for the risk gate to
stop and ask, so it is scored everywhere. A classifier that says yes to
everything asks permission for everything, which is how a confirmation prompt
becomes furniture.

A case can accept more than one intent. Some sentences genuinely work either
way - "what is this on my screen" is answered by looking and pointing, which
is what both SHOW and ACT would do - and forcing a single label onto a real
ambiguity makes the score a worse description of the behaviour, not a better
one. `also_fine` is for that, and only that: if a route would change nothing
the user could notice, it belongs there, and if it would, it does not.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..agent.router import Intent, ModelRouter
from ..language.routing import correct


@dataclass(frozen=True)
class Case:
    said: str
    intent: Intent
    also_fine: tuple[Intent, ...] = ()
    # None means not scored. Only meaningful on ANSWER cases - see above.
    needs_screen: bool | None = None
    risky: bool | None = None
    note: str = ""

    @property
    def acceptable(self) -> tuple[Intent, ...]:
        return (self.intent, *self.also_fine)


CASES = (
    # --- answer: being told is all they want ------------------------------
    Case("what is the capital of france", Intent.ANSWER,
         needs_screen=False, risky=False),
    Case("who won the world cup in 2018", Intent.ANSWER,
         needs_screen=False, risky=False),
    Case("what can you do", Intent.ANSWER, needs_screen=False, risky=False),

    # --- answer, and it does need the screen. These are the cases the
    # --- screenshot exists for, and none of them trip `needs_to_look`.
    Case("is there a typo in this paragraph", Intent.ANSWER,
         also_fine=(Intent.SHOW,), needs_screen=True, risky=False),
    Case("what font is the heading in", Intent.ANSWER,
         also_fine=(Intent.SHOW,), needs_screen=True, risky=False),
    Case("summarise what i am reading", Intent.ANSWER, needs_screen=True,
         risky=False),

    # --- show: asking HOW, so instructions rather than an action ----------
    Case("how do i change my dns", Intent.SHOW, risky=False,
         note="mined no route at all, which is why recipes carry one"),
    Case("where is the bluetooth setting", Intent.SHOW, risky=False),
    Case("show me how to add a slide", Intent.SHOW, risky=False),
    Case("how do i check my mail", Intent.SHOW, risky=False,
         note="the one case the connector promotion must NOT touch"),
    Case("teach me how to minimize the vs code", Intent.SHOW, risky=False),
    Case("i dont know how to change dark mode to light mode", Intent.SHOW,
         risky=False, note="the walkthrough case - one step, then watch"),
    Case("take me to my calendar settings", Intent.SHOW, risky=False),
    Case("show me the white queen on my screen", Intent.SHOW,
         also_fine=(Intent.ACT,), risky=False,
         note="marking is Risk.SAFE, so either route only ever draws"),

    # --- act: one thing, carried out --------------------------------------
    Case("open notepad", Intent.ACT, risky=False),
    Case("click the close button", Intent.ACT, risky=False),
    Case("minimise this window", Intent.ACT, risky=False,
         note="british spelling against an american button label"),
    Case("switch to my chrome window", Intent.ACT, risky=False,
         note="show refuses open_app, so it would point at the taskbar"),
    Case("click the address bar and search for cats", Intent.ACT,
         also_fine=(Intent.PLAN,),
         note="names the desktop, so it must stay off the research path"),

    # --- act, by correction: shaped like a question, answerable only by a
    # --- tool. Each of these was answered out of training data once.
    Case("whats in my inbox", Intent.ACT, risky=False),
    Case("show me my inbox", Intent.ACT, risky=False),
    Case("whats on my calendar today", Intent.ACT, risky=False),
    # PLAN is allowed here on purpose, not to flatter the score. Research
    # takes about thirty seconds, and a foreground research turn blocking the
    # voice loop for half a minute with no icon to watch is a bug this project
    # already fixed once. ACT answers sooner; PLAN hands it a window. Both are
    # defensible, so neither is counted wrong.
    Case("do a research on gpu prices in india", Intent.ACT,
         also_fine=(Intent.PLAN,), risky=False),
    Case("look up the best laptops under fifty thousand", Intent.ACT,
         risky=False),
    Case("whats the weather in delhi", Intent.ACT, risky=False),

    # --- act, by correction: answerable only by LOOKING -------------------
    Case("what should be my next move", Intent.ACT, also_fine=(Intent.SHOW,),
         risky=False, note="advised a knight onto a square it was already on"),
    Case("so i want the best move i could do here", Intent.ACT,
         also_fine=(Intent.SHOW,), risky=False),
    Case("whose turn is it", Intent.ACT, also_fine=(Intent.SHOW,),
         risky=False),
    Case("what is this on my screen", Intent.ACT, also_fine=(Intent.SHOW,),
         risky=False),

    # --- plan: more than one job wearing one sentence ---------------------
    Case("find research on solar panel costs and put it in a spreadsheet",
         Intent.PLAN, risky=False, note="the documented Phase 2 demo"),
    Case("gpu prices in india, put it in a spreadsheet", Intent.PLAN,
         risky=False, note="a plan although the word research was never said"),
    Case("open notepad and type hello there", Intent.PLAN, risky=False),
    Case("open notepad then type hi my name is srija", Intent.PLAN,
         risky=False),
    Case("make a deck about the history of computing", Intent.PLAN,
         risky=False),

    # --- AS THE MICROPHONE ACTUALLY PRODUCED THEM ------------------------
    #
    # Everything above this line is a sentence somebody typed. These were
    # spoken out loud at this machine and printed by the running app, damage
    # and all - and the damage is the point. The suites kept passing while
    # live runs kept failing, because clean text is not the input.
    Case("Hey, can you open Blender for me?", Intent.ACT, risky=False),
    Case("Open Blender for me?", Intent.ACT, risky=False,
         note="a flat instruction, punctuated as a question by prosody"),
    Case("Open blender.", Intent.ACT, risky=False,
         note="scored SHOW live, and the cat said to say 'do it' instead"),
    Case("Can you open notepad for B?", Intent.ACT, risky=False,
         note="'for me'"),
    Case("An you open a new tab on it?", Intent.ACT, risky=False,
         note="'Can you'"),
    Case("Minimizes the notepad.", Intent.ACT, risky=False,
         note="'minimise the notepad'"),
    Case("Minimize VS code for me and open blender.", Intent.PLAN,
         also_fine=(Intent.ACT,), risky=False),
    Case("S in my inbox.", Intent.ACT, risky=False,
         note="'what is in my inbox' - the first two words gone"),

    Case("Can you teach me how to create a new file here?", Intent.SHOW,
         risky=False),
    Case("Hey, can you teach me how to open new files here?", Intent.SHOW,
         risky=False),
    Case("How do I do a simple animation of a ball jumping on a single "
         "plane?", Intent.SHOW, risky=False,
         note="answered 'i cannot find instructions' with Blender open"),
    Case("Okay, now I want to make a ball bounce over a plane animation. "
         "Tell me how to do it.", Intent.SHOW, risky=False),
    Case("Can you tell me how to delete everything here?", Intent.SHOW,
         risky=False,
         note="asking HOW to delete is not asking for a deletion"),

    Case("Where do I get personalization?", Intent.SHOW, risky=False),
    Case("Where is the keyframe section? Can you show me it?", Intent.SHOW,
         risky=False),
    Case("Can you tell me where do I set the interpolation?", Intent.SHOW,
         risky=False),
    Case("Can you direct me towards the river?", Intent.SHOW,
         also_fine=(Intent.ACT,), risky=False,
         note="'the liver', on an anatomy page - a homophone no layer fixes"),
    Case("Can you see it? What's on my screen?", Intent.ACT,
         also_fine=(Intent.SHOW,), risky=False),
    Case("Catch me up?", Intent.ACT, also_fine=(Intent.ANSWER,), risky=False),

    # --- risky: things that leave the machine or cannot be taken back -----
    Case("delete all my emails", Intent.ACT, also_fine=(Intent.PLAN,),
         risky=True),
    Case("send that email to arun", Intent.ACT, risky=True),
    Case("uninstall chrome", Intent.ACT, also_fine=(Intent.PLAN,),
         risky=True),
)


@dataclass
class Outcome:
    case: Case
    raw: Intent
    final: Intent
    needs_screen: bool
    risky: bool
    milliseconds: float
    why: str

    @property
    def intent_hit(self) -> bool:
        return self.final in self.case.acceptable

    @property
    def screen_hit(self) -> bool | None:
        """None when nothing reads it for this sentence."""
        if self.case.needs_screen is None or self.case.intent is not Intent.ANSWER:
            return None
        return self.needs_screen is self.case.needs_screen

    @property
    def risk_hit(self) -> bool | None:
        if self.case.risky is None:
            return None
        return self.risky is self.case.risky


def _wrongness(outcome: "Outcome") -> str:
    """What was wrong about one run, as a string, for grouping."""
    wrong: list[str] = []
    if not outcome.intent_hit:
        wrong.append(f"{outcome.case.intent.value} -> {outcome.final.value}")
    if outcome.screen_hit is False:
        wrong.append(f"needs_screen={outcome.needs_screen}")
    if outcome.risk_hit is False:
        wrong.append(f"risky={outcome.risky}")
    return ", ".join(wrong)


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)
    source: str = ""

    def _scored(self, name: str) -> list[Outcome]:
        return [o for o in self.outcomes if getattr(o, name) is not None]

    def summary(self) -> str:
        lines: list[str] = ["", f"  routing - {self.source}", ""]

        hits = [o for o in self.outcomes if o.intent_hit]
        corrected = [o for o in self.outcomes if o.raw is not o.final]
        alone = [o for o in self.outcomes if o.raw in o.case.acceptable]
        lines.append(f"  intent        {len(hits)}/{len(self.outcomes)}"
                     f"   (model alone {len(alone)},"
                     f" corrections fired {len(corrected)})")

        screened = self._scored("screen_hit")
        if screened:
            got = len([o for o in screened if o.screen_hit])
            lines.append(f"  needs_screen  {got}/{len(screened)}"
                         f"   (answer turns only - nothing else reads it)")

        risked = self._scored("risk_hit")
        if risked:
            got = len([o for o in risked if o.risk_hit])
            lines.append(f"  risky         {got}/{len(risked)}")

        times = sorted(o.milliseconds for o in self.outcomes)
        if times:
            lines.append("")
            lines.append(f"  latency       {times[len(times) // 2]:.0f}ms "
                         f"median, {times[0]:.0f}-{times[-1]:.0f}ms")

        misses = [o for o in self.outcomes
                  if not o.intent_hit or o.screen_hit is False
                  or o.risk_hit is False]
        if misses:
            # Grouped by sentence, with how often it went wrong. Over several
            # runs the difference between "wrong" and "wrong sometimes" is the
            # whole diagnosis: one is a rule to write and the other is a model
            # that does not hold still.
            runs: dict[str, int] = {}
            for outcome in self.outcomes:
                runs[outcome.case.said] = runs.get(outcome.case.said, 0) + 1

            lines.extend(["", "  wrong:"])
            seen: set[tuple[str, str]] = set()
            for outcome in misses:
                key = (outcome.case.said, _wrongness(outcome))
                if key in seen:
                    continue
                seen.add(key)
                how_often = len([o for o in misses
                                 if (o.case.said, _wrongness(o)) == key])
                total = runs[outcome.case.said]
                lines.append(f"    {outcome.case.said!r}")
                lines.append(f"      {key[1]}"
                             + (f"   ({how_often} of {total} runs)"
                                if total > 1 else ""))
                if outcome.case.note:
                    lines.append(f"      ({outcome.case.note})")
        else:
            lines.extend(["", "  nothing wrong."])

        # Which corrections are carrying the router, and which never fire. A
        # rule nobody triggers is dead weight; one that triggers on half the
        # set is a prompt that should be fixed instead of patched.
        if corrected:
            fired: dict[str, int] = {}
            for outcome in corrected:
                reason = (f"{outcome.raw.value} -> {outcome.final.value}"
                          f"  {outcome.why}")
                fired[reason] = fired.get(reason, 0) + 1
            lines.extend(["", "  corrections that fired:"])
            for reason, count in sorted(fired.items(),
                                        key=lambda pair: -pair[1]):
                lines.append(f"    {count}x  {reason}")

        return "\n".join(lines) + "\n"


def run(cases=CASES, router: ModelRouter | None = None,
        repeat: int = 1) -> Report:
    """Route every case and score it. One request each, no screen, no mic.

    `repeat` runs the whole set several times. Worth doing occasionally:
    **temperature=0 is not determinism**, and two sentences in this set were
    measured changing route between runs - "open notepad then type hi my name
    is srija" about one run in five, "summarise what i am reading" about two
    in five. A single pass of 36/37 is a sample, not a property, and that is
    the real cost of a chat model in a place that used to hold a scorer.
    """
    routing = router or ModelRouter()
    outcomes: list[Outcome] = []

    for _ in range(max(1, repeat)):
        for case in cases:
            # No conversation context. Each sentence is scored standing
            # alone, which is the harder case and the one the corrections
            # exist for - a set that fed each case its own ideal history
            # would be measuring the history rather than the router.
            raw = routing.route(case.said)
            final, why = correct(raw, case.said)
            outcomes.append(Outcome(
                case=case,
                raw=raw.intent,
                final=final.intent,
                needs_screen=raw.needs_screen,
                risky=raw.risky,
                milliseconds=raw.milliseconds,
                why=why,
            ))

    return Report(outcomes=outcomes, source=routing.model)


def main() -> int:
    import argparse

    from ..config import load_env
    from ..console import use_utf8_console

    use_utf8_console()
    load_env()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=1,
                        help="run the whole set N times, to see what is "
                             "intermittent rather than wrong")
    options = parser.parse_args()

    print()
    print(f"  replaying {len(CASES)} spoken sentences"
          f"{f', {options.repeat} times' if options.repeat > 1 else ''}...")
    started = time.perf_counter()
    report = run(repeat=options.repeat)
    print(report.summary())
    print(f"  {time.perf_counter() - started:.1f}s total")
    print()

    return 1 if any(not o.intent_hit for o in report.outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
