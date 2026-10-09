"""Does the teaching model know the application, or does it need the manual?

    meow teaching

Before building a lookup it is worth knowing whether knowledge is the
bottleneck at all - the measured lesson of this project is that three obvious
improvements to grounding bought exactly nothing, and saying so cost less than
shipping them would have.

**Ground truth comes from the application itself**, so there is no
hand-labelling and nothing to argue about. Every question is built from the
installed Blender's own keymap: pick an operator whose label identifies one
binding, ask the model which key does it, and compare.

Two conditions:

    from memory      the question alone, which is what happened before
    with the manual  the same question, with the handful of lines that
                     word-overlap retrieval actually surfaces

The second is the honest test of a lookup rather than of an oracle: the model
is handed what `Manual.about()` returns, including the near-misses, and if
retrieval cannot find the answer then this condition does not get it either.

MEASURED, gpt-4o-mini, Blender 4.1.1, 26 questions:

    from memory        17/26    (65%)
    with the manual    26/26    on this run

Two of the nine it fixed would have DAMAGED the user's work: asked how to
delete a KEYFRAME the model said X, which in Object Mode is `object.delete`
and removes the object; asked how to MIRROR it said Shift+D, which
duplicates. Those are not "slightly wrong", they are a learner losing work by
following the instruction.

⚠ **THE FIRST VERSION OF THIS MEASURED NOTHING USEFUL and said 50% -> 85%.**
It keyed uniqueness on the operator and its properties rather than on the
words the model is SHOWN, so the set filled with unanswerable questions -
"Hide Collection" is one label with a binding per collection, so 1 through 9
were nine rows and one question. See `questions()`.

⚠ **I WROTE A CAVEAT HERE SAYING THE WIN NEEDED THE CONTEXT. IT WAS WRONG,
AND MEASURING IT WAS CHEAP.** The reasoning was sound - a shortcut is only
correct inside one context, X deletes the object in Object Mode and opens a
menu in Edit Mode, and nothing in the accessibility tree says which mode
Blender is in. The conclusion did not follow: every retrieved line CARRIES
its own context, so the model picks the right one without being told.

Re-measured on 24 questions with the context never stated, all for the mode
Blender was really in:

    from memory                        11/24   (46%)
    the manual, context NOT stated     24/24
    the manual plus a live read        24/24

So the honest headline is better than the first one, not worse. And reading
Blender's live state - which was built to close this gap - closes nothing,
because there was no gap. That is the fourth measured improvement in this
project to buy exactly zero.
"""

from __future__ import annotations

import re

# Contexts a learner is plausibly in. A Sequencer binding is real and is not
# what "how do i move this" means to somebody looking at the default scene.
TEACHING_CONTEXTS = ("Object Mode", "Mesh", "3D View", "Object Non-modal",
                     "Window", "Screen", "Frames")

# Operators whose label cannot identify one binding, so a question built from
# them tests the question rather than the model. `wm.call_menu` is bound to
# dozens of keys and every one of them is labelled "Call Menu".
UNFAIR_OPERATORS = ("wm.call_menu", "wm.call_panel", "wm.context",
                    "wm.call_menu_pie")

HOW_MANY = 26

ASK = """You are teaching somebody Blender out loud.

They are working in: {context}
They ask how to do this: {task}
({description})

Answer with the KEY COMBINATION only - nothing else, no sentence, no
explanation. For example: ctrl+b   or   shift+a   or   tab
If it is a mouse button say so, like: middle click
"""

WITH_MANUAL = """You are teaching somebody Blender out loud.

{manual}

They are working in: {context}
They ask how to do this: {task}
({description})

Answer with the KEY COMBINATION only - nothing else, no sentence, no
explanation. For example: ctrl+b   or   shift+a   or   tab
If it is a mouse button say so, like: middle click
"""


def _tokens(keys: str) -> frozenset:
    """A key combination as a comparable set, so "Ctrl + B" matches "ctrl+b".

    Graded on a SET rather than a string: the model writes "Shift+Ctrl+Z" and
    the keymap says "ctrl+shift+Z", and marking that wrong would measure
    formatting.
    """
    text = (keys or "").lower().strip().strip(".")
    text = text.replace("numpad_", "numpad ").replace("_", " ")
    for word in ("press ", "hold ", " key", "then ", "the "):
        text = text.replace(word, " ")
    out = set()
    for part in re.split(r"[+\-\s]+", text):
        part = part.strip()
        if not part:
            continue
        out.add({"control": "ctrl", "cmd": "ctrl", "command": "ctrl",
                 "option": "alt", "del": "delete", "return": "enter",
                 "esc": "escape", "spacebar": "space"}.get(part, part))
    return frozenset(out)


def questions(manual, how_many: int = HOW_MANY) -> list:
    """Questions whose answer is one unambiguous key, built from the manual.

    Built from the data rather than written by hand, so the set cannot be
    quietly chosen to be the ones the model happens to get right. Spread
    across contexts, or it would be entirely Window shortcuts.
    """
    # KEYED ON WHAT THE MODEL IS SHOWN, which is the label and the context -
    # NOT on the operator and its properties. That was the first version and
    # it quietly filled the set with unanswerable questions: "Hide Collection"
    # is one label with a binding per collection, so 1 through 9 are nine
    # distinct (operator, properties) rows and one identical question. Same
    # for "View Orbit" on four numpad keys, "Select" with and without extend,
    # and "Play Animation" forwards and in reverse. The model was being asked
    # to pick one of nine and marked wrong for picking another.
    #
    # A question is only fair when the words the model sees have exactly one
    # answer, so that is what the uniqueness test has to be over.
    by_label: dict = {}
    for shortcut in manual.shortcuts:
        if shortcut.context not in TEACHING_CONTEXTS:
            continue
        if shortcut.operator.startswith(UNFAIR_OPERATORS):
            continue
        if not shortcut.description or not shortcut.label:
            continue
        if shortcut.description == "(undocumented operator)":
            continue
        by_label.setdefault((shortcut.label, shortcut.context),
                            []).append(shortcut)

    pool = []
    for (label, context), found in by_label.items():
        if len({shortcut.keys for shortcut in found}) != 1:
            continue
        # And it must be unique across the WHOLE manual too: a label bound to
        # one key in Mesh and a different one in the UV Editor is a question
        # about which editor, not about the key.
        elsewhere = {s.keys for s in manual.shortcuts if s.label == label}
        if len(elsewhere) != 1:
            continue
        pool.append({"context": context, "task": label,
                     "description": found[0].description,
                     "operator": found[0].operator,
                     "truth": found[0].keys})

    pool.sort(key=lambda row: (row["context"], row["operator"]))
    spread, taken = [], set()
    for context in TEACHING_CONTEXTS:
        here = [row for row in pool if row["context"] == context]
        step = max(1, len(here) // 4)
        for row in here[::step]:
            if row["operator"] in taken:
                continue
            spread.append(row)
            taken.add(row["operator"])
            if len(spread) >= how_many:
                return spread
    return spread


def _unfair(rows) -> set:
    """Tasks whose LABEL does not identify one binding, across the set."""
    labels: dict = {}
    for row in rows:
        labels.setdefault(row["task"], set()).add(row["truth"])
    return {task for task, truths in labels.items() if len(truths) > 1}


def main() -> int:
    from ..agent.harness import MODEL
    from ..config import openai_api_key
    from ..knowledge import manuals

    library = manuals.load()
    manual = library.for_window("blender.exe")
    if manual is None:
        print()
        print("  No Blender reference yet. Build it with:")
        print("    python scripts/blender_reference.py")
        print()
        return 1

    asked = questions(manual)
    if len(asked) < 4:
        print(f"\n  only {len(asked)} usable questions in the manual\n")
        return 1

    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(model=MODEL, api_key=openai_api_key(),
                       max_completion_tokens=24, temperature=0)

    print(f"\n  teaching - {manual.application} {manual.version}, "
          f"{len(asked)} shortcut questions, model {MODEL}\n")
    print(f"  {'context':<18} {'task':<28} {'truth':<18} "
          f"{'memory':<16} manual")
    print("  " + "-" * 94)

    rows = []
    for question in asked:
        expected = _tokens(question["truth"])
        answers = {}
        for name, template in (("memory", ASK), ("manual", WITH_MANUAL)):
            found = manual.to_prompt(
                f"{question['task']} {question['description']}")
            prompt = template.format(
                manual=found if name == "manual" else "",
                context=question["context"], task=question["task"],
                description=question["description"])
            try:
                reply = model.invoke(prompt)
                answers[name] = str(getattr(reply, "content", "")).strip()
            except Exception as error:  # noqa: BLE001 - a failure is a miss
                answers[name] = f"<{type(error).__name__}>"
        rows.append((question, answers))
        right = {name: _tokens(said) == expected
                 for name, said in answers.items()}
        print(f"  {question['context']:<18} {question['task'][:27]:<28} "
              f"{question['truth']:<18} "
              f"{answers['memory'][:14]:<16}{answers['manual'][:14]}"
              f"   {'' if right['manual'] else '<'}", flush=True)

    unfair = _unfair([q for q, _ in rows])
    print()
    for label, keep in (("all", lambda q: True),
                        ("fair only", lambda q: q["task"] not in unfair)):
        kept = [(q, a) for q, a in rows if keep(q)]
        if not kept:
            continue
        counts = {}
        for name in ("memory", "manual"):
            counts[name] = sum(
                1 for q, a in kept if _tokens(a[name]) == _tokens(q["truth"]))
        total = len(kept)
        print(f"  {label:<12} n={total:<3} "
              f"from memory {counts['memory']:>3}/{total} "
              f"({counts['memory'] / total:.0%})   "
              f"with the manual {counts['manual']:>3}/{total} "
              f"({counts['manual'] / total:.0%})")
    if unfair:
        print(f"\n  unfair, because the label names more than one binding: "
              f"{', '.join(sorted(unfair))}")

    fixed = [(q, a) for q, a in rows
             if _tokens(a["memory"]) != _tokens(q["truth"])
             and _tokens(a["manual"]) == _tokens(q["truth"])]
    if fixed:
        print("\n  memory was wrong and the manual was right:")
        for question, answers in fixed:
            print(f"    {question['task'][:34]:<36} said "
                  f"{answers['memory'][:14]:<16} truth {question['truth']}")

    both = [(q, a) for q, a in rows
            if _tokens(a["memory"]) != _tokens(q["truth"])
            and _tokens(a["manual"]) != _tokens(q["truth"])]
    if both:
        print("\n  both wrong - where retrieval did not surface it either:")
        for question, answers in both:
            print(f"    {question['task'][:34]:<36} said "
                  f"{answers['memory'][:14]:<16}/"
                  f"{answers['manual'][:14]:<16} truth {question['truth']}")
    print()
    return 0
