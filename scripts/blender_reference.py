"""Build Meow's Blender reference, from the Blender that is installed.

    python scripts/blender_reference.py

It finds blender.exe, runs it headless, and writes
`%LOCALAPPDATA%/Meow/manuals/blender.json` - 2,501 shortcuts across 267
contexts and 2,297 operators, each with the description Blender ships for it.

**Why from the installed copy rather than from the web.** A page about
shortcuts is about whichever version its author had, and Blender moves its
keymap between releases. This is what 4.1.1 on this machine does, which is the
only thing the person sitting in front of it cares about. It is also the
better trust position: `find_how_to` has to treat a fetched page as untrusted
and may only take LOOK-FOR candidates from it, and none of that applies here.

**It is method C, used for knowledge rather than for modelling.** A background
Blender process with no window, reading its own keymap definition. The visible
application is not involved and does not need to be running.

Re-run it after upgrading Blender. Nothing checks: a keymap that is one minor
version stale is still far better than the model's memory, which the
measurement in `meow/knowledge/manuals.py` puts at about half right.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Runs INSIDE Blender, so it may only import bpy and the standard library.
# Passed as a string rather than shipped as a file because it has to live
# wherever Blender can read it, and an inline expression has no path.
INSIDE = r"""
import json, os, sys
import bpy

def shortcuts():
    # The keymap is DEFINED in Python and shipped with Blender. Reading
    # `wm.keyconfigs` in background mode returns almost nothing - the items
    # are built lazily, and a probe got 12 of them - so the definition is
    # read instead, which is complete and needs no window.
    here = os.path.join(bpy.utils.resource_path("LOCAL"), "scripts",
                        "presets", "keyconfig", "keymap_data")
    sys.path.insert(0, here)
    import blender_default

    rows = []
    for name, _poll, contents in blender_default.generate_keymaps():
        for entry in contents.get("items", []):
            if not isinstance(entry, tuple) or len(entry) < 2:
                continue
            operator, event = entry[0], entry[1]
            if not isinstance(operator, str) or not isinstance(event, dict):
                continue
            key = event.get("type")
            if not key:
                continue
            # A binding on key RELEASE or on a drag is real and is not what
            # anybody means by "which key does this".
            if event.get("value") not in (None, "PRESS", "CLICK",
                                          "DOUBLE_CLICK"):
                continue
            held = [m for m in ("ctrl", "shift", "alt", "oskey")
                    if event.get(m) is True]
            properties = {}
            if len(entry) > 2 and isinstance(entry[2], dict):
                pairs = entry[2].get("properties")
                if isinstance(pairs, (list, tuple)):
                    for pair in pairs:
                        try:
                            properties[str(pair[0])] = str(pair[1])
                        except Exception:
                            continue
            rows.append({"context": name,
                         "keys": "+".join(held + [str(key)]),
                         "operator": operator,
                         "properties": properties})
    return rows


def operators():
    rows = []
    for group_name in dir(bpy.ops):
        if group_name.startswith("_"):
            continue
        group = getattr(bpy.ops, group_name)
        for name in dir(group):
            if name.startswith("_"):
                continue
            try:
                rna = getattr(group, name).get_rna_type()
                label, description = rna.name or "", rna.description or ""
            except Exception:
                continue
            rows.append({"operator": "%s.%s" % (group_name, name),
                         "label": label, "description": description})
    return rows


where = sys.argv[-1]
bundle = {"application": "blender", "executable": "blender.exe",
          "version": bpy.app.version_string,
          "shortcuts": shortcuts(), "operators": operators()}
with open(where, "w", encoding="utf-8") as handle:
    json.dump(bundle, handle, indent=1)
print("MEOW_WROTE %s %s %d %d" % (where, bundle["version"],
                                  len(bundle["shortcuts"]),
                                  len(bundle["operators"])))
"""

# How Blender names a key against how somebody says it out loud. Applied when
# the manual is written rather than when it is read, so nothing downstream has
# to know that Blender calls the 1 key ONE.
SPOKEN = {
    "ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5",
    "SIX": "6", "SEVEN": "7", "EIGHT": "8", "NINE": "9", "ZERO": "0",
    "DEL": "delete", "RET": "enter", "BACK_SPACE": "backspace",
    "PERIOD": ".", "COMMA": ",", "SLASH": "/", "SEMI_COLON": ";",
    "ACCENT_GRAVE": "`", "LEFT_BRACKET": "[", "RIGHT_BRACKET": "]",
    "MINUS": "-", "EQUAL": "=", "APP": "menu key",
    "LEFTMOUSE": "left click", "RIGHTMOUSE": "right click",
    "MIDDLEMOUSE": "middle click", "WHEELUPMOUSE": "scroll up",
    "WHEELDOWNMOUSE": "scroll down", "WHEELINMOUSE": "scroll in",
    "WHEELOUTMOUSE": "scroll out",
    "LEFT_ARROW": "left arrow", "RIGHT_ARROW": "right arrow",
    "UP_ARROW": "up arrow", "DOWN_ARROW": "down arrow",
}

# Contexts a learner is plausibly in. Everything is KEPT - a Sequencer binding
# is real - but these are the ones a question about the default scene is most
# likely to be about, so they are listed first in the file and win ties.
LIKELY_FIRST = ("Object Mode", "Mesh", "3D View", "Object Non-modal",
                "Window", "Screen", "Frames", "Sculpt", "Grease Pencil")


def find_blender() -> Path | None:
    """blender.exe, from the usual places then from the Start menu."""
    guesses = []
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramW6432", r"C:\Program Files")):
        base = Path(root) / "Blender Foundation"
        if not base.is_dir():
            continue
        # Newest first, so an install with 3.6 and 4.1 side by side gives 4.1.
        for folder in sorted(base.iterdir(), reverse=True):
            guesses.append(folder / "blender.exe")
    for guess in guesses:
        if guess.is_file():
            return guess

    try:
        from meow.desktop import apps

        found = apps.find_application("blender")
        if found is not None:
            target = str(getattr(found, "target", "") or "")
            if target.lower().endswith("blender.exe") and Path(target).is_file():
                return Path(target)
    except Exception:  # noqa: BLE001
        pass
    return None


def speak(keys: str) -> str:
    return "+".join(SPOKEN.get(part, part) for part in keys.split("+"))


def main() -> int:
    from meow.knowledge import manuals

    blender = find_blender()
    if blender is None:
        print("\n  Could not find blender.exe. Install Blender, or pass the "
              "path:\n    python scripts/blender_reference.py <path>\n")
        return 1
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        blender = Path(sys.argv[1])

    raw = manuals.folder() / "blender.raw.json"
    print(f"\n  {blender}")
    print("  reading its keymap, headless...")

    result = subprocess.run(
        [str(blender), "--background", "--factory-startup",
         "--python-expr", INSIDE, "--", str(raw)],
        capture_output=True, text=True, timeout=300)

    wrote = [line for line in (result.stdout or "").splitlines()
             if line.startswith("MEOW_WROTE")]
    if not wrote or not raw.is_file():
        print("\n  Blender did not write the reference. Its output:\n")
        for line in (result.stdout or "").splitlines()[-12:]:
            print("   ", line)
        for line in (result.stderr or "").splitlines()[-12:]:
            print("   ", line)
        return 1

    bundle = json.loads(raw.read_text(encoding="utf-8"))

    # Keys into how a person says them, and the likely contexts first. Done
    # HERE rather than inside Blender so the translation is in the project's
    # own code, where it can be read and corrected.
    rank = {name: index for index, name in enumerate(LIKELY_FIRST)}
    for row in bundle["shortcuts"]:
        row["keys"] = speak(row["keys"])
    bundle["shortcuts"].sort(
        key=lambda row: (rank.get(row["context"], len(LIKELY_FIRST)),
                         row["context"], row["operator"]))

    out = manuals.folder() / "blender.json"
    out.write_text(json.dumps(bundle, indent=1), encoding="utf-8")
    raw.unlink(missing_ok=True)

    library = manuals.load()
    manual = library.for_window("blender.exe")
    print(f"\n  wrote {out}")
    print(f"  Blender {bundle['version']}: "
          f"{len(bundle['shortcuts']):,} shortcuts, "
          f"{len(bundle['operators']):,} operators, "
          f"{out.stat().st_size // 1024:,}KB")
    if manual is None:
        print("  but it did not load back - see meow doctor")
        return 1
    for question in ("how do i bevel an edge",
                     "how do i add a loop cut",
                     "how do i delete a keyframe"):
        print(f"\n  {question!r}")
        for line in manual.about(question, 3):
            print(f"    {line.describe()[:96]}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
