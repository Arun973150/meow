"""One tray icon per running agent.

When Meow hands work over, that agent gets its own icon in the tray. Clicking
it opens the window straight onto that agent's conversation - what it is doing
now and everything it has said, as an ordinary chat.

**Why an icon each rather than one icon and a list.** Two agents running is the
normal case and they are doing unrelated things; a single icon that opens "the
window" makes the user find the right conversation every time, which is the
work the icon was supposed to save. The tray is also the only place a
background job can be visible without taking screen space from whatever the
user is actually doing.

**The icon goes when the work finishes**, with a notification saying so. A tray
that accumulates an icon per job ever run is a tray people stop looking at. The
conversation itself is never lost - it is in the main window, and in the
database, permanently. The icon is a handle on something *running*, not a
record of something that ran.
"""

from __future__ import annotations

import os
import time
from typing import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from meow.chat import icons
from meow.conversations import Conversation, Store

# How long a finishing notification stays up. Long enough to read a sentence,
# short enough not to sit over someone's work.
NOTIFICATION_MILLISECONDS = 6000

# The window runs detached with no console, so when an icon does not appear
# there is nowhere for it to say why. Set MEOW_TRAY_LOG to a path and it
# records whether the icon was never created, was created and then hidden by
# Windows, or the process never saw the agent at all - three very different
# problems that look identical from the taskbar.
LOG = os.environ.get("MEOW_TRAY_LOG", "")


def note(text: str) -> None:
    if not LOG:
        return
    try:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(time.strftime("%H:%M:%S") + "  " + text + chr(10))
    except Exception:  # noqa: BLE001
        pass


class AgentTrays:
    """Keeps the tray in step with which agents are running.

    Polled from the window's existing timer rather than given its own, so
    there is one clock in this process and no question about which of two
    timers saw a change first.
    """

    def __init__(self, store: Store, reveal: Callable[[int], None],
                 quit_action: Callable[[], None]) -> None:
        self.store = store
        self.reveal = reveal
        self.quit_action = quit_action
        # Held as attributes, not locals: a QSystemTrayIcon that goes out of
        # scope is garbage collected and silently vanishes from the tray,
        # which looks exactly like the agent having finished.
        self.trays: dict[int, QSystemTrayIcon] = {}
        self.menus: dict[int, QMenu] = {}
        self.titles: dict[int, str] = {}

    def _build(self, conversation: Conversation) -> None:
        tray = QSystemTrayIcon(
            icons.for_conversation(conversation.icon, live=True))
        tray.setToolTip(f"{conversation.title} - working")

        menu = QMenu()
        open_action = QAction("Open this conversation", menu)
        quit_all = QAction("Quit Meow's window", menu)
        menu.addAction(open_action)
        menu.addSeparator()
        menu.addAction(quit_all)
        tray.setContextMenu(menu)

        # Bound now, so each icon opens ITS conversation rather than whichever
        # one the loop variable happened to hold when the click arrived.
        conversation_id = conversation.id
        open_action.triggered.connect(lambda: self.reveal(conversation_id))
        quit_all.triggered.connect(lambda: self.quit_action())
        tray.activated.connect(
            lambda reason, cid=conversation_id:
            self.reveal(cid) if reason == QSystemTrayIcon.Trigger else None)

        tray.show()
        note(f"built icon for {conversation_id} ({conversation.title!r}) "
             f"visible={tray.isVisible()} "
             f"available={QSystemTrayIcon.isSystemTrayAvailable()}")
        # The menu is kept too. Qt does not take ownership of a context menu,
        # so dropping the reference leaves the icon with a right-click that
        # does nothing.
        self.trays[conversation_id] = tray
        self.menus[conversation_id] = menu
        self.titles[conversation_id] = conversation.title

    def _retire(self, conversation_id: int, finished: bool) -> None:
        tray = self.trays.pop(conversation_id, None)
        self.menus.pop(conversation_id, None)
        title = self.titles.pop(conversation_id, "a task")
        if tray is None:
            return
        if finished:
            # Said through the icon that is about to disappear, so the
            # notification comes from the thing the user was watching.
            tray.showMessage("Meow", f"{title} - finished",
                             icons.cat_icon(icons.LIVE),
                             NOTIFICATION_MILLISECONDS)
        note(f"retired icon for {conversation_id} finished={finished}")
        tray.hide()
        tray.setContextMenu(None)

    def refresh(self) -> None:
        """Add an icon for each new agent, remove them as they finish."""
        try:
            conversations = self.store.conversations()
        except Exception:  # noqa: BLE001 - the tray is not worth a crash
            return

        running = {c.id: c for c in conversations
                   if c.kind == "task" and c.live}
        if LOG and (running or self.trays):
            note(f"refresh: live={sorted(running)} icons={sorted(self.trays)}")

        for conversation_id, conversation in running.items():
            if conversation_id not in self.trays:
                self._build(conversation)
            else:
                # The title changes when a conversation is renamed after its
                # first line, so the tooltip follows it.
                self.trays[conversation_id].setToolTip(
                    f"{conversation.title} - working")
                self.titles[conversation_id] = conversation.title

        for conversation_id in list(self.trays):
            if conversation_id not in running:
                self._retire(conversation_id, finished=True)

    def close(self) -> None:
        for conversation_id in list(self.trays):
            self._retire(conversation_id, finished=False)
