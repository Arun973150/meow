"""The accessibility tree - Phase 1.1 and 1.4. This is the thesis.

Vision grounding asks a model to guess where a button is from pixels. This asks
Windows, and gets back an exact rectangle and a handle that can be invoked.

**How it asks matters more than that it asks.** Walking the tree in Python -
reading name, role and rectangle for every node, one cross-process call at a
time - costs about 1.2ms per element, so a real window is seconds of work. The
native `FindAllBuildCache` asks UIA for *only the control types we care about*
and pre-fetches their properties in one round trip. Measured on the same
window, same machine:

    python breadth-first walk    5000 elements, 728 actionable, 6170 ms
    FindAllBuildCache             756 actionable, 262 ms          23.5x faster

The speed is not the important part. The walk had to be truncated, so it
returned an arbitrary slice of the tree; the native call returns *all* of it.

**And it dissolves most of the selection problem.** The spike counted 819
actionable elements in VS Code and that number set up Phase 1.4 as "which 150
of 819?". But 819 counts elements that are scrolled out of view, inside
collapsed menus, or have no name at all - none of which a user can refer to or
a model can use. Filtering to on-screen AND named leaves **120**, which is
already under the budget. Ranking still exists for the windows where it is not.

`TRUNCATED` is still a regime, and still means "the tree was not seen". Never
classify a walk that gave up. See docs/02-grounding.md for what that cost.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import Enum

import uiautomation as auto

user32 = ctypes.WinDLL("user32", use_last_error=True)

# From UIAutomationClient.h. Hardcoded rather than imported from the generated
# comtypes module, whose name varies between machines and regenerates on
# install.
UIA_NAME = 30005
UIA_CONTROL_TYPE = 30003
UIA_BOUNDING_RECTANGLE = 30001
UIA_IS_OFFSCREEN = 30022
UIA_IS_ENABLED = 30010
TREE_SCOPE_DESCENDANTS = 4

# Control types worth asking for. Everything absent from this list is
# decoration: visible, but not something an agent can operate.
CONTROL_TYPES = {
    50000: "button", 50002: "checkbox", 50003: "combo", 50004: "edit",
    50005: "link", 50007: "item", 50010: "menubar", 50011: "menuitem",
    50013: "radio", 50015: "slider", 50016: "spinner", 50019: "tab",
    50021: "toolbar", 50023: "treeitem", 50029: "dataitem", 50031: "splitbutton",
}

DIGEST_LIMIT = 150

# A window that takes longer than this to answer is one we fall back to vision
# for. Measured: a heavy window is ~260ms, so this is generous rather than tight.
QUERY_DEADLINE_SECONDS = 1.2


class Regime(Enum):
    RICH = "rich"
    DENSE = "dense"
    EMPTY = "empty"
    TRUNCATED = "truncated"


@dataclass(frozen=True)
class Element:
    """One thing on screen that can be acted on."""

    name: str
    role: str
    left: int
    top: int
    right: int
    bottom: int
    enabled: bool = True

    # The live UIA element. Kept so a control can be operated through the
    # accessibility API - Invoke() presses a button without the pointer going
    # anywhere, which works on a window that is not even in front. Vision can
    # never do that, and it is a bigger difference than the hit rate.
    #
    # Excluded from equality and repr: it is a COM pointer, comparing two is
    # meaningless and printing one is noise.
    node: object | None = field(default=None, compare=False, repr=False)

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def centre(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    def describe(self) -> str:
        """One line for the model: role, name, and where to click.

        The centre rather than the full rectangle - it is what a click needs
        and it is half the tokens of four corners.
        """
        x, y = self.centre
        suffix = "" if self.enabled else " (disabled)"
        return f'{self.role} "{self.name}" {x},{y}{suffix}'


@dataclass
class WindowDigest:
    """What the foreground window offers, ready for a model."""

    app: str
    title: str
    elements: list[Element]
    regime: Regime
    total_found: int
    usable_found: int
    query_seconds: float

    @property
    def truncated(self) -> bool:
        return self.regime is Regime.TRUNCATED

    def to_prompt(self) -> str:
        lines = [
            f"Controls currently on screen in {self.app} "
            f"({self.title[:60]}). Coordinates are screen pixels:"
        ]
        lines.extend(element.describe() for element in self.elements)
        if self.usable_found > len(self.elements):
            lines.append(
                f"({len(self.elements)} of {self.usable_found} shown, ranked by "
                f"prominence and nearness to the pointer.)"
            )
        return "\n".join(lines)

    def find(self, text: str) -> Element | None:
        """Best element matching a description.

        Exact, then prefix, then substring - ranked rather than first-hit,
        because "save" should find Save before it finds "Save As...".
        """
        wanted = " ".join(text.lower().split())
        if not wanted:
            return None
        for test in (
            lambda name: name == wanted,
            lambda name: name.startswith(wanted),
            lambda name: wanted in name,
        ):
            matches = [e for e in self.elements if test(e.name.lower())]
            if matches:
                return min(matches, key=lambda e: len(e.name))
        return None

    def suggest(self, text: str, limit: int = 6) -> list[str]:
        """Names close to what was asked for.

        Returned with every miss, because "there is no control called that" is
        a dead end and the agent answers it by guessing again. Six guesses is
        how a single request exhausted its whole call budget: it asked for
        "terminal control", which does not exist, and never learned that
        "Terminal (Ctrl+`)" does.
        """
        wanted = set(text.lower().split())
        scored = []
        for element in self.elements:
            words = set(element.name.lower().split())
            overlap = len(wanted & words)
            if overlap:
                scored.append((overlap, -len(element.name), element.name))
        scored.sort(reverse=True)
        if scored:
            return [name for _, _, name in scored[:limit]]
        # Nothing shares a word. Offer the most prominent controls instead, so
        # the reply is still useful rather than only apologetic.
        return [element.name for element in self.elements[:limit]]


class _Query:
    """Reusable condition and cache request.

    Built once. Constructing the OR condition over sixteen control types costs
    a COM call per type, which is not much but is pure waste on every turn of a
    voice loop.
    """

    _instance: "_Query | None" = None

    def __init__(self) -> None:
        self.client = auto.uiautomation._AutomationClient.instance().IUIAutomation

        conditions = [
            self.client.CreatePropertyCondition(UIA_CONTROL_TYPE, type_id)
            for type_id in CONTROL_TYPES
        ]
        condition = conditions[0]
        for extra in conditions[1:]:
            condition = self.client.CreateOrCondition(condition, extra)
        self.condition = condition

        self.cache = self.client.CreateCacheRequest()
        for prop in (UIA_NAME, UIA_CONTROL_TYPE, UIA_BOUNDING_RECTANGLE,
                     UIA_IS_OFFSCREEN, UIA_IS_ENABLED):
            self.cache.AddProperty(prop)

    @classmethod
    def instance(cls) -> "_Query":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


def foreground_window():
    """The window the user is working in, or None."""
    handle = user32.GetForegroundWindow()
    if not handle:
        return None
    try:
        return auto.ControlFromHandle(handle)
    except Exception:  # noqa: BLE001 - a window can close between the calls
        return None


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_process_names: dict[int, str] = {}


def _process_name(process_id: int) -> str:
    """Executable name for a pid, via the API rather than by spawning tasklist.

    The subprocess version cost around 200ms - most of the digest's total time,
    spent starting a program to answer a question one API call answers. Cached
    as well, since a window keeps its pid for its whole life.
    """
    cached = _process_names.get(process_id)
    if cached is not None:
        return cached

    name = f"pid:{process_id}"
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if handle:
        try:
            buffer = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            if kernel32.QueryFullProcessImageNameW(
                    handle, 0, buffer, ctypes.byref(size)):
                name = buffer.value.rsplit("\\", 1)[-1] or name
        finally:
            kernel32.CloseHandle(handle)

    _process_names[process_id] = name
    return name


def query_elements(window) -> tuple[list[Element], int]:
    """Every actionable, on-screen, named control in a window.

    Returns (usable elements, total actionable found). The gap between those
    two numbers is the interesting one: it is how much of the tree exists but
    cannot be referred to.
    """
    query = _Query.instance()
    found = window.Element.FindAllBuildCache(
        TREE_SCOPE_DESCENDANTS, query.condition, query.cache)

    usable: list[Element] = []
    total = found.Length
    for index in range(total):
        try:
            node = found.GetElement(index)
            if node.CachedIsOffscreen:
                # Scrolled out of view, or inside a collapsed menu. Present in
                # the tree, invisible to the user, and useless to point at.
                continue
            name = (node.CachedName or "").strip()
            if not name:
                # An unnamed control cannot be asked for by name and cannot be
                # matched if it were. It is a hole in the digest, not an entry.
                continue
            rect = node.CachedBoundingRectangle
            if rect.right <= rect.left or rect.bottom <= rect.top:
                continue
            usable.append(Element(
                name=name,
                role=CONTROL_TYPES.get(node.CachedControlType, "control"),
                left=int(rect.left), top=int(rect.top),
                right=int(rect.right), bottom=int(rect.bottom),
                enabled=bool(node.CachedIsEnabled),
                node=node,
            ))
        except Exception:  # noqa: BLE001 - elements vanish mid-iteration
            continue

    return usable, total


def score(element: Element, cursor: tuple[int, int], window_area: int) -> float:
    """How likely is this the thing the user means?

    This function IS Phase 1.4. Every weight is a guess until the evaluation in
    04-evaluation.md measures it, so they are named and separated rather than
    folded into one clever expression.
    """
    points = 1.0

    # Near the pointer. People talk about what they are looking at, and where
    # they are pointing is the best proxy available for that.
    distance = max(
        1.0,
        ((element.centre[0] - cursor[0]) ** 2
         + (element.centre[1] - cursor[1]) ** 2) ** 0.5,
    )
    points += 2.5 / (1.0 + distance / 400.0)

    # Disabled controls are visible but cannot be used. Worth mentioning,
    # never worth ranking first.
    if not element.enabled:
        points -= 1.5

    # An element covering most of the window is a container that happens to be
    # actionable. Pointing at its centre points at nothing in particular.
    if window_area > 0:
        coverage = element.area / window_area
        if coverage > 0.5:
            points -= 2.0
        elif coverage > 0.25:
            points -= 0.8

    # A very long name is usually a paragraph that happens to be exposed as a
    # control, not something anyone will say out loud.
    if len(element.name) > 60:
        points -= 1.0

    return points


def classify(usable: int, total: int, query_seconds: float) -> Regime:
    """RICH, DENSE, EMPTY - or TRUNCATED, a refusal to classify."""
    if query_seconds > QUERY_DEADLINE_SECONDS:
        # The window did not answer in time, so its tree was not seen. Saying
        # anything else is the bug that sent this project chasing a Chromium
        # wake mechanism that never existed.
        return Regime.TRUNCATED
    if usable == 0:
        return Regime.EMPTY
    if total > 400:
        return Regime.DENSE
    return Regime.RICH


def digest_foreground(cursor: tuple[int, int] | None = None,
                      limit: int = DIGEST_LIMIT) -> WindowDigest | None:
    """The foreground window as a ranked, capped list of controls."""
    window = foreground_window()
    if window is None:
        return None

    if cursor is None:
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        cursor = (point.x, point.y)

    started = time.perf_counter()
    try:
        app = _process_name(window.ProcessId)
        title = (window.Name or "").strip()
        rect = window.BoundingRectangle
        window_area = rect.width() * rect.height() if rect else 0
    except Exception:  # noqa: BLE001
        app, title, window_area = "?", "", 0

    try:
        usable, total = query_elements(window)
    except Exception:  # noqa: BLE001 - a dying window is not a crash
        usable, total = [], 0

    query_seconds = time.perf_counter() - started

    ranked = sorted(usable,
                    key=lambda element: score(element, cursor, window_area),
                    reverse=True)

    return WindowDigest(
        app=app,
        title=title,
        elements=ranked[:limit],
        regime=classify(len(usable), total, query_seconds),
        total_found=total,
        usable_found=len(usable),
        query_seconds=query_seconds,
    )
