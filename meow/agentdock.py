"""An icon beside the cat for each running agent. Click it to watch that agent.

This started in the system tray and had to move, which is worth writing down
because the reason is not a bug anyone can fix.

**Windows 11 hides new tray icons by default**, behind the chevron, and it
remembers that decision per icon. A tray icon created fresh for each agent is
one Windows has never seen, so every single one starts hidden - the user would
have to go and un-hide each agent individually, forever. The diagnostics were
unambiguous about the code working: `built icon ... visible=True
available=True`, created and accepted, and still not on screen. A design whose
visibility depends on a setting the user must repeat per job is the wrong
design, however correct the code.

So the icons live next to the cat, where Meow already owns the pixels. They
appear when work is handed over and go when it finishes, and clicking one opens
the chat window on that agent's conversation.

**These overlays are NOT click-through**, unlike every other one in the project.
The cat must never intercept a click meant for the window underneath it; an
icon whose entire purpose is being clicked must. That is the only place in Meow
where `WS_EX_TRANSPARENT` is deliberately off.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from .platform.overlay import Bounds, Overlay

user32 = ctypes.windll.user32

# Big enough to hit without aiming, small enough to sit beside the cat without
# becoming the thing you look at.
ICON_SIZE = 34
GAP = 6

# Pinned to the top right of the screen, NOT to the cat. Anchoring them to
# the cat meant they slid across the desktop every time it moved to follow the
# pointer, so an icon was never in the same place twice and clicking one meant
# chasing it. A status indicator has to be somewhere you can look without
# finding it first.
MARGIN = 18

# How far down the right edge the column starts. NOT at the very top: these
# overlays intercept clicks rather than passing them through, and the top right
# corner of a maximised window is its close, maximise and minimise buttons -
# an icon there does not merely sit over the close button, it eats the click.
# 96px clears a title bar and a tab strip at normal scaling.
TOP_OFFSET = 96

WM_LBUTTONUP = 0x0202


@dataclass
class DockedAgent:
    """One running agent, its icon, and where that icon is."""

    conversation_id: int
    title: str
    kind: str                      # "magnifier" or "gear"
    overlay: Overlay | None = None
    bounds: Bounds | None = None


def draw_icon(kind: str, size: int = ICON_SIZE,
              spin: float = 0.0) -> Image.Image:
    """The icon, drawn rather than loaded - the same argument as the cat.

    `spin` turns the mark slowly while the agent works, which is the only
    signal that anything is happening: the icon is small and has no room for
    text, and a still icon beside a still cat reads as finished.
    """
    scale = 4  # drawn large and downsampled, which is cheaper than antialiasing
    canvas = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    edge = size * scale
    middle = edge // 2

    # A filled disc so the icon reads against any wallpaper, light or dark.
    draw.ellipse([2 * scale, 2 * scale, edge - 2 * scale, edge - 2 * scale],
                 fill=(30, 30, 32, 235), outline=(123, 201, 111, 255),
                 width=2 * scale)

    ink = (232, 230, 227, 255)
    if kind == "magnifier":
        radius = int(edge * 0.20)
        centre_x = middle - int(edge * 0.05)
        centre_y = middle - int(edge * 0.05)
        draw.ellipse([centre_x - radius, centre_y - radius,
                      centre_x + radius, centre_y + radius],
                     outline=ink, width=2 * scale)
        draw.line([centre_x + int(radius * 0.7), centre_y + int(radius * 0.7),
                   centre_x + int(radius * 1.6), centre_y + int(radius * 1.6)],
                  fill=ink, width=2 * scale)
    else:
        import math

        radius = int(edge * 0.17)
        draw.ellipse([middle - radius, middle - radius,
                      middle + radius, middle + radius],
                     outline=ink, width=2 * scale)
        for index in range(8):
            angle = spin + index * math.pi / 4
            inner = radius + 2 * scale
            outer = radius + 6 * scale
            draw.line([middle + inner * math.cos(angle),
                       middle + inner * math.sin(angle),
                       middle + outer * math.cos(angle),
                       middle + outer * math.sin(angle)],
                      fill=ink, width=2 * scale)

    return canvas.resize((size, size), Image.LANCZOS)


class AgentDock:
    """Keeps one clickable icon on screen per running agent."""

    def __init__(self, window_script: Path | None = None) -> None:
        self.agents: dict[int, DockedAgent] = {}
        self.window_script = window_script or (
            Path(__file__).resolve().parent / "chat" / "window.py")

    # --- what is running -------------------------------------------------

    def sync(self, running: list[tuple[int, str, str]]) -> None:
        """Match the dock to the agents that are running right now.

        Takes plain tuples rather than a Store, so the caller decides where
        the truth lives and this stays testable without a database.
        """
        wanted = {conversation_id: (title, kind)
                  for conversation_id, title, kind in running}

        for conversation_id in list(self.agents):
            if conversation_id not in wanted:
                self._remove(conversation_id)

        for conversation_id, (title, kind) in wanted.items():
            if conversation_id not in self.agents:
                self.agents[conversation_id] = DockedAgent(
                    conversation_id=conversation_id, title=title, kind=kind)

    def _remove(self, conversation_id: int) -> None:
        agent = self.agents.pop(conversation_id, None)
        if agent is not None and agent.overlay is not None:
            agent.overlay.close()

    def close(self) -> None:
        for conversation_id in list(self.agents):
            self._remove(conversation_id)

    # --- drawing ---------------------------------------------------------

    def layout(self, monitor) -> None:
        """Stack the icons down from the top right corner.

        Fixed, and deliberately not near the cat. The cat moves - it follows
        the pointer and returns home - and icons anchored to it slid across
        the desktop with it, so one was never in the same place twice and
        clicking it meant chasing it first.

        Downwards from the corner, so a second agent appears below the first
        rather than pushing it, and the one that started first stays put.
        """
        left = monitor.work_right - ICON_SIZE - MARGIN
        for index, agent in enumerate(self.agents.values()):
            top = monitor.work_top + TOP_OFFSET + index * (ICON_SIZE + GAP)
            agent.bounds = Bounds(left, top, ICON_SIZE, ICON_SIZE)

    def draw(self, phase: float) -> None:
        from .cat import rgba_to_premultiplied_bgra

        for agent in self.agents.values():
            if agent.bounds is None:
                continue
            if agent.overlay is None:
                # NOT click-through. This is the one overlay in Meow that is
                # meant to receive clicks rather than pass them on.
                agent.overlay = Overlay(agent.bounds, click_through=False)
                agent.overlay.show()
            agent.overlay.set_bounds(agent.bounds)
            image = draw_icon(agent.kind, spin=phase * 1.2)
            agent.overlay.draw(rgba_to_premultiplied_bgra(image))

    # --- clicking --------------------------------------------------------

    def clicked(self) -> int | None:
        """Which agent's icon was just clicked, if any.

        Polled from the render loop rather than handled in a window procedure.
        The overlay's procedure is shared by every overlay in the process and
        runs on whichever thread pumped the message, so routing a click back to
        the right agent from inside it means locking; asking "was a button
        released over one of my rectangles" costs one call and needs none.
        """
        if not self.agents:
            # Nothing docked, so nothing to consume. Returning before the
            # GetAsyncKeyState call matters: the low bit is "pressed since
            # anyone last asked", process-wide, so reading it when there are
            # no icons would quietly eat a click another part of Meow might
            # one day want. Nothing else in the project calls it today, and
            # this keeps that true by accident rather than by luck.
            return None

        # Low bit: pressed since the last call. Polled at 60fps from the render
        # loop, so "since last call" is one frame.
        pressed = user32.GetAsyncKeyState(0x01) & 0x0001  # VK_LBUTTON
        if not pressed:
            return None

        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))

        for agent in self.agents.values():
            bounds = agent.bounds
            if bounds is None:
                continue
            if (bounds.left <= point.x <= bounds.left + bounds.width
                    and bounds.top <= point.y <= bounds.top + bounds.height):
                return agent.conversation_id
        return None

    def open_conversation(self, conversation_id: int) -> None:
        """Bring up the chat window on this agent's conversation.

        A second window process is started rather than signalled. The running
        one is detached and has no channel back, and Qt's single-instance
        handling is more moving parts than this is worth - the new process
        opens on the right conversation, and the old one is still sitting in
        the tray doing no harm.
        """
        try:
            subprocess.Popen(
                [sys.executable, str(self.window_script),
                 "--show", "--open", str(conversation_id)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:  # noqa: BLE001 - an icon that will not open is not
            pass           # a reason to stop listening
