"""Can the accessibility tree describe a whole application, not just a screen?

    python spikes/map_probe.py                 the window in front, in 3s
    python spikes/map_probe.py --menus         also expand the menu bar
    python spikes/map_probe.py --app blender   launch it first, then wait

Three questions, because the answers turn out to be different:

1. **Is the digest leaving anything on the table?** It is a FLAT query over
   sixteen whitelisted control types, capped at 150, with offscreen elements
   dropped - not a tree walk. So this counts the whole tree, every role,
   with depth, and compares.

2. **Can the MENU BAR be enumerated?** The 44 labelled targets say the tree in
   creative applications sees menus and almost nothing else: Photoshop 96
   menuitems, Illustrator 80, Premiere 40, Resolve 45, and not one panel,
   tool or canvas. A collapsed menu's items are `IsOffscreen`, which the
   digest deliberately skips - so the digest sees "File Edit Image..." and
   stops. If `ExpandCollapsePattern` opens them, the application's entire
   command surface is readable, once, for free.

3. **Is Blender really blind?** AGENTS.md says five elements, measured before
   the whitelist and the cap were what they are now. Worth re-checking with
   the filter off, because "the tree is empty" and "our query is narrow" look
   identical from the outside and only one of them is a platform limit.

NOTHING HERE IS CLICKED. `Expand` is an accessibility call, not a press, and
every menu it opens is collapsed again and Escape sent at the end. It is still
visible on screen while it runs, which is why this is a probe rather than
something the voice loop does.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uiautomation as auto  # noqa: E402

from meow.desktop.uia import (  # noqa: E402
    CONTROL_TYPES,
    DIGEST_LIMIT,
    digest_foreground,
    foreground_window,
)

user32 = ctypes.WinDLL("user32", use_last_error=True)

# Deep enough for Electron, which nests content about thirty levels down - a
# cap of 12 once made VS Code look like a platform limitation and cost this
# project two days. Fifty is what `uia.py` settled on.
MAX_DEPTH = 50

# The whole walk, not a voice turn. A map is built once per application and
# cached forever, so it can afford seconds where a digest cannot afford 300ms.
WALK_BUDGET_SECONDS = 25.0

# Named by UIA. The digest keeps sixteen of these and calls the rest
# decoration; for a MAP the containers are the interesting part, because
# "which panel is this in" is the structure a flat list throws away.
CONTAINERS = {"pane", "group", "window", "custom", "document", "tabitem",
              "toolbar", "menubar", "list", "tree", "table", "header"}

EXPAND_COLLAPSE_PATTERN = 10005


def role_of(node) -> str:
    try:
        return CONTROL_TYPES.get(node.ControlType, _raw_role(node))
    except Exception:  # noqa: BLE001
        return "?"


def _raw_role(node) -> str:
    """UIA's own name for a type the digest does not whitelist."""
    try:
        return (node.ControlTypeName or "?").replace("ControlType", "").lower()
    except Exception:  # noqa: BLE001
        return "?"


# --- 1. the whole tree, unfiltered ------------------------------------------


def walk(root, deadline):
    """Every node under root: (depth, role, name, has_rect). Breadth-first.

    Breadth-first on purpose. A depth-first walk that runs out of budget has
    explored one branch to the bottom and knows nothing about the rest, which
    is the worst possible partial answer about the SHAPE of a tree.
    """
    seen = []
    frontier = [(0, root)]
    truncated = False
    while frontier:
        if time.perf_counter() > deadline:
            truncated = True
            break
        depth, node = frontier.pop(0)
        try:
            name = (node.Name or "").strip()
            rect = node.BoundingRectangle
            has_rect = bool(rect and rect.right > rect.left
                            and rect.bottom > rect.top)
            offscreen = bool(node.IsOffscreen)
        except Exception:  # noqa: BLE001 - nodes vanish mid-walk
            continue
        seen.append((depth, role_of(node), name, has_rect, offscreen))
        if depth >= MAX_DEPTH:
            continue
        try:
            frontier.extend((depth + 1, child) for child in node.GetChildren())
        except Exception:  # noqa: BLE001
            continue
    return seen, truncated


def report_tree(window) -> None:
    started = time.perf_counter()
    nodes, truncated = walk(window, started + WALK_BUDGET_SECONDS)
    seconds = time.perf_counter() - started

    named = [n for n in nodes if n[2]]
    onscreen = [n for n in named if n[3] and not n[4]]
    whitelisted = [n for n in onscreen if n[1] in CONTROL_TYPES.values()]
    containers = [n for n in nodes if n[1] in CONTAINERS]

    print(f"\n  THE WHOLE TREE{' (ran out of budget)' if truncated else ''}"
          f"   {seconds:.1f}s")
    print(f"    nodes                      {len(nodes):>6}")
    print(f"    named                      {len(named):>6}")
    print(f"    named, on screen           {len(onscreen):>6}")
    print(f"    ...and a whitelisted role  {len(whitelisted):>6}   "
          f"<- what the digest can see")
    print(f"    containers (panels etc)    {len(containers):>6}")
    print(f"    deepest                    {max((n[0] for n in nodes), default=0):>6}")

    roles = Counter(n[1] for n in nodes)
    print("\n    every role in the tree, commonest first:")
    for role, count in roles.most_common(14):
        mark = " " if role in CONTROL_TYPES.values() else "*"
        print(f"      {mark} {role:<18} {count:>5}")
    print("      (* = not whitelisted, so invisible to the digest)")

    # The question the digest cannot answer about itself.
    digest = digest_foreground()
    if digest is not None:
        print(f"\n    the digest right now: {len(digest.elements)} of "
              f"{digest.usable_found} usable, {digest.total_found} actionable, "
              f"{digest.regime.value}, {digest.query_seconds * 1000:.0f}ms")
        if digest.usable_found > DIGEST_LIMIT:
            print(f"    {digest.usable_found - DIGEST_LIMIT} usable controls "
                  f"were CUT by the {DIGEST_LIMIT} cap")


# --- 2. the menu bar, expanded ----------------------------------------------


def menu_bars(window):
    """Every menubar in the window, by searching the tree rather than guessing."""
    found = []
    frontier = [window]
    while frontier:
        node = frontier.pop(0)
        try:
            if role_of(node) == "menubar":
                found.append(node)
                continue          # its items are its children; do not recurse past
            frontier.extend(node.GetChildren())
        except Exception:  # noqa: BLE001
            continue
    return found


def expandable(node):
    try:
        return node.GetPattern(EXPAND_COLLAPSE_PATTERN)
    except Exception:  # noqa: BLE001
        return None


def popped_up(before):
    """A menu window that appeared since `before`, or None.

    A classic Win32 popup menu is its own TOP-LEVEL WINDOW of class `#32768`,
    parented to the desktop - not a child of the item that opened it. Reading
    the item's children after `Expand` returns the ITEM, which is how the
    first version of this produced "File > File" and looked like the
    mechanism failing.

    **A WinUI/XAML application does neither.** Measured on Notepad:
    `ExpandCollapseState` goes 0 -> 1, so the menu really opens, and the items
    appear nowhere on the desktop and nowhere under the item. They are in the
    APPLICATION WINDOW's own tree, one level deeper than the menu bar. So the
    reliable method is to re-walk the window after expanding and take what is
    new - see `read_menu`, which does that and found 17 items where 4 were
    visible before.
    """
    try:
        root = auto.GetRootControl()
        for child in root.GetChildren():
            try:
                if child.ClassName == "#32768" and id(child) not in before:
                    return child
                if role_of(child) == "menu":
                    return child
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        return None
    return None


def _menu_windows():
    """Which popup windows exist right now, so a new one can be told apart."""
    present = set()
    try:
        for child in auto.GetRootControl().GetChildren():
            try:
                if child.ClassName == "#32768":
                    present.add(id(child))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return present


def read_menu(item, depth=0, limit=3):
    """One menu, opened, read, and closed again. Returns a flat route list."""
    routes = []
    # Every property read can raise: a menu item is a live COM pointer and
    # the popup it belongs to can be torn down between two calls. The whole
    # of this function is therefore defensive, and a lost menu costs one
    # branch of the map rather than the run.
    try:
        name = (item.Name or "").strip()
    except Exception:  # noqa: BLE001
        return routes
    pattern = expandable(item)
    if pattern is None:
        return routes

    before = _menu_windows()
    try:
        pattern.Expand()
    except Exception:  # noqa: BLE001 - a disabled menu refuses, which is fine
        return routes
    time.sleep(0.18)              # the popup has to actually appear

    popup = popped_up(before)
    # Fall back to the item's own children: owner-drawn menus (Office, some
    # Adobe dialogs) really do nest them, and then there is no popup window.
    holders = [popup] if popup is not None else [item]
    for holder in holders:
        try:
            children = holder.GetChildren()
        except Exception:  # noqa: BLE001
            continue
        for child in children:
            child_name = (child.Name or "").strip()
            if not child_name or child_name == name:
                continue
            routes.append(f"{name} > {child_name}")
            if depth + 1 < limit and expandable(child) is not None:
                routes.extend(read_menu(child, depth + 1, limit))

    try:
        pattern.Collapse()
    except Exception:  # noqa: BLE001
        pass
    return routes


def report_menus(window) -> None:
    bars = menu_bars(window)
    print(f"\n  THE MENU BAR   {len(bars)} found")
    if not bars:
        print("    none. Either the application draws its own menus (Blender,"
              "\n    a game) or they are not in the tree at all.")
        return

    started = time.perf_counter()
    every_route = []
    for bar in bars:
        try:
            items = bar.GetChildren()
        except Exception:  # noqa: BLE001
            continue
        tops = []
        for candidate in items:
            try:
                if (candidate.Name or "").strip():
                    tops.append(candidate)
            except Exception:  # noqa: BLE001
                continue
        print(f"    top level ({len(tops)}): "
              + ", ".join((i.Name or "").strip() for i in tops[:14]))
        for item in tops:
            try:
                every_route.extend(read_menu(item))
            except Exception as error:  # noqa: BLE001
                print(f"      ({type(error).__name__} reading a menu)")
    seconds = time.perf_counter() - started

    # Escape twice, in case a menu was left open by a refused Collapse.
    for _ in range(2):
        auto.SendKeys("{Esc}", waitTime=0.05)

    print(f"\n    {len(every_route)} routes read in {seconds:.1f}s")
    if every_route:
        print("    a sample:")
        step = max(1, len(every_route) // 12)
        for route in every_route[::step][:12]:
            print(f"      {route}")
        characters = sum(len(route) + 1 for route in every_route)
        print(f"\n    the whole map is {characters:,} characters, "
              f"about {characters // 4:,} tokens")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--menus", action="store_true",
                        help="also expand and read the menu bar")
    parser.add_argument("--app", default="",
                        help="launch this application first")
    parser.add_argument("--wait", type=float, default=3.0,
                        help="seconds to let you focus the window")
    arguments = parser.parse_args()

    from meow.platform.dpi import enable_per_monitor_dpi_awareness

    enable_per_monitor_dpi_awareness()

    if arguments.app:
        from meow.desktop import apps

        found = apps.find_application(arguments.app)
        if found is None:
            print(f"  no application matching {arguments.app!r}")
            return 1
        print(f"  launching {found.name}...")
        apps.launch(found)
        time.sleep(max(arguments.wait, 12.0))
    else:
        print(f"  focus the window you want, {arguments.wait:.0f}s...")
        time.sleep(arguments.wait)

    window = foreground_window()
    if window is None:
        print("  no foreground window")
        return 1
    try:
        print(f"\n  {window.Name[:70]!r}")
    except Exception:  # noqa: BLE001
        pass

    report_tree(window)
    if arguments.menus:
        report_menus(window)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
