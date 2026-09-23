"""Hand-labelling targets, so the evaluation stops marking its own homework.

    meow label

The existing ablation samples its tasks FROM the UIA digest and then asks UIA
to find them, which is why it scores 30/30. The task is the control's own
name - `locate("Close")` against a digest that contains a control called
"Close" - and the ground truth is that same control's rectangle. Nothing about
that measures whether the tree is better than pixels; it measures that a
dictionary lookup works.

This produces the other kind of task. A person points at something on their
own screen, presses F8, and types what they would CALL it - "the button that
closes this window", not "Close". Two things then hold that did not before:

    the words are the user's, not the digest's
    the point is where a human said it was, not where the tree said

Both strategies are scored on the same pair afterwards, and neither has seen
either. If UIA drops below 30/30 against honest targets, that is a real
result and a more interesting one than the tautology.

The screenshot and the digest are saved WITH each label, so the set can be
replayed offline as many times as you like without the window still being
open. That is not cheating: the digest is what the system legitimately has in
front of it. What it must not have is the answer.
"""

from __future__ import annotations

import ctypes
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

VIRTUAL_KEY_F8 = 0x77
VIRTUAL_KEY_ESCAPE = 0x1B
POLL_SECONDS = 0.03


@dataclass
class Label:
    """One hand-marked target."""

    description: str          # what a person would call it, in their words
    point: tuple[int, int]    # where a person said it is
    app: str
    window: str
    digest_file: str = ""
    screenshot_file: str = ""
    # What the tree happened to call the control under that point, recorded
    # for ANALYSIS only and never given to a strategy. It answers "was this
    # target even in the digest?", which is the difference between UIA missing
    # something it could see and being asked for something it never had.
    nearest_uia_name: str = ""
    # image pixels -> screen pixels is a DIVISION by scale, then add origin.
    scale: float = 1.0
    origin: tuple = (0, 0)
    marked_at: float = field(default_factory=time.time)


def folder() -> Path:
    """Where a labelled set lives. Visible on purpose - a study's ground truth
    is something you want to be able to read, correct and delete by hand.
    """
    from ..storage import documents

    place = documents() / "evaluation"
    place.mkdir(parents=True, exist_ok=True)
    return place


def _pressed(key: int) -> bool:
    """True on the transition to down, not while held."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(key) & 0x0001)


def _drain(key: int) -> None:
    """Throw away a stale was-pressed bit.

    GetAsyncKeyState's low bit accumulates since anyone last asked, so the
    first read covers however long the program was not looking - the same trap
    the agent dock hit, where a click from minutes earlier opened a window
    nobody asked for.
    """
    ctypes.windll.user32.GetAsyncKeyState(key)


def load(name: str = "labels.json") -> list[Label]:
    """An existing labelled set, or an empty list."""
    path = folder() / name
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    labels = []
    for item in raw:
        item["point"] = tuple(item["point"])
        if "origin" in item:
            item["origin"] = tuple(item["origin"])
        labels.append(Label(**item))
    return labels


def save(labels: list[Label], name: str = "labels.json") -> Path:
    path = folder() / name
    path.write_text(
        json.dumps([asdict(label) for label in labels], indent=2),
        encoding="utf-8")
    return path


def _capture_context(index: int) -> tuple[str, str, str, str, str]:
    """Freeze what the strategies will later be given: digest and pixels."""
    from ..desktop.uia import digest_foreground
    from ..platform.capture import capture_screens

    app = window = digest_file = screenshot_file = nearest = ""
    scale = 1.0
    origin = (0, 0)
    digest = digest_foreground()
    if digest is not None:
        app, window = digest.app, digest.title
        digest_file = f"digest-{index:03d}.json"
        (folder() / digest_file).write_text(
            json.dumps({
                "app": digest.app,
                "title": digest.title,
                "regime": digest.regime.name,
                "total_found": digest.total_found,
                "elements": [
                    {"name": element.name, "role": element.role,
                     "left": element.left, "top": element.top,
                     "right": element.right, "bottom": element.bottom,
                     "enabled": element.enabled}
                    for element in digest.elements],
            }, indent=2), encoding="utf-8")

    try:
        shots = capture_screens()
        if shots:
            # `.image`, a PIL Image - NOT `.data`. Writing `.data` raised an
            # AttributeError that the except below swallowed, so every label
            # was saved with no screenshot at all and the vision arm of the
            # evaluation could never have run. Silent, and indistinguishable
            # from success.
            screenshot_file = f"screen-{index:03d}.png"
            shots[0].image.convert("RGB").save(folder() / screenshot_file)
            # Without these the picture cannot be mapped back to the screen.
            # A model answers in IMAGE pixels; the hand-marked point is in
            # SCREEN pixels; `scale` and the monitor origin are the only way
            # to compare them.
            scale = shots[0].scale
            origin = (shots[0].monitor.left, shots[0].monitor.top)
    except Exception:  # noqa: BLE001 - a missing screenshot is not fatal
        screenshot_file = ""

    return app, window, digest_file, screenshot_file, nearest, scale, origin


def _name_under(point: tuple[int, int]) -> str:
    """What the tree calls whatever is under that point. Analysis only."""
    from ..desktop.uia import digest_foreground

    digest = digest_foreground()
    if digest is None:
        return ""
    x, y = point
    smallest, best = None, ""
    for element in digest.elements:
        if element.left <= x <= element.right and element.top <= y <= element.bottom:
            area = (element.right - element.left) * (element.bottom - element.top)
            if smallest is None or area < smallest:
                smallest, best = area, element.name
    return best


def main() -> int:
    from ..console import quiet_library_warnings, use_utf8_console
    from ..platform.dpi import enable_per_monitor_dpi_awareness
    from ..platform.monitors import get_cursor_position

    use_utf8_console()
    quiet_library_warnings()
    # Without this the cursor position is reported in the wrong coordinate
    # space on a scaled monitor, and every label lands somewhere else.
    enable_per_monitor_dpi_awareness()

    labels = load()
    print(__doc__.split("\n\n")[0])
    print()
    print(f"  {len(labels)} label(s) already saved in {folder()}")
    print()
    print("  Open the application you want to label.")
    print("  Put the mouse ON a control, then press F8 - WITHOUT clicking it.")
    print("  Type what you would CALL it, in your own words, and press Enter.")
    print("  Press Esc to finish.")
    print()
    print("  Describe it the way you would say it out loud. Copying the")
    print("  control's label back is exactly what this set exists to avoid.")
    print()

    _drain(VIRTUAL_KEY_F8)
    _drain(VIRTUAL_KEY_ESCAPE)

    while True:
        if _pressed(VIRTUAL_KEY_ESCAPE):
            break
        if not _pressed(VIRTUAL_KEY_F8):
            time.sleep(POLL_SECONDS)
            continue

        point = get_cursor_position()
        index = len(labels)
        (app, window, digest_file, screenshot_file, _,
         scale, origin) = _capture_context(index)
        nearest = _name_under(point)

        print(f"  marked {point} in {app or 'unknown'}"
              f"{' - ' + window[:40] if window else ''}")
        try:
            description = input("  what do you call it? ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not description:
            print("  skipped - no description")
            continue

        labels.append(Label(
            description=description, point=point, app=app, window=window,
            digest_file=digest_file, screenshot_file=screenshot_file,
            nearest_uia_name=nearest, scale=scale, origin=tuple(origin)))
        save(labels)
        print(f"  saved {len(labels)}. Esc to finish, F8 to mark another.")
        print()
        _drain(VIRTUAL_KEY_F8)

    path = save(labels)
    print()
    print(f"  {len(labels)} label(s) in {path}")
    if labels:
        print("  now run:  meow evaluate --labelled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
