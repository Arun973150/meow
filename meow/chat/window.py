"""The chat window - a conversation list, a transcript, and a tray icon.

**Its own process, and that is forced rather than chosen.** Qt wants the main
thread for its event loop, and the cat's overlay already has it: a layered
Win32 window redrawn at 60fps, which is not something to interleave with
another framework's loop. So the window is launched as a subprocess and talks
to the rest of Meow through the SQLite store in `meow/conversations.py`.

That turns out to be the better shape anyway. The window can crash, be closed,
or never be opened at all, and the voice loop neither notices nor cares - it is
writing to a database either way. Nothing about whether the cat can hear you
depends on a GUI being alive.

**It polls rather than being pushed to.** Every 400ms it asks the store for the
newest message id, which is one indexed row, and only re-reads if that changed.
A socket would be fewer wakeups and one more thing to get wrong on a machine
where the writer may not be running yet, may have died, or may be restarted
mid-conversation.

**Hidden until asked for.** It starts in the tray. Closing the window hides it
rather than quitting, because a companion that has to be relaunched from a
terminal is not a companion.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction, QColor, QCursor, QDesktopServices, QFont,
)
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QStyleOptionViewItem,
    QMainWindow, QMenu, QSplitter, QSystemTrayIcon, QVBoxLayout,
    QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from meow.chat import icons
from meow.chat.bubbles import (
    TEXT, WHO, MessageDelegate, file_path, hit_yes, is_ask, is_file,
)
from meow.chat.trays import AgentTrays
from meow.conversations import Conversation, Store

POLL_MILLISECONDS = 400

# Dark by default. The cat lives on top of whatever the user is doing, usually
# an editor or a browser at night, and a white panel opening over that is the
# kind of thing people close once and never open again.
STYLE = """
QMainWindow, QWidget { background: #1b1a19; color: #e8e6e3; }
QSplitter::handle { background: #2a2927; }
QListWidget {
    background: #211f1e; border: none; padding: 6px;
    font-size: 13px; outline: none;
}
QListWidget::item { padding: 9px 10px; border-radius: 7px; color: #c9c6c2; }
QListWidget::item:selected { background: #34322f; color: #ffffff; }
QListWidget::item:hover { background: #2a2927; }
QListWidget#transcript {
    background: #1b1a19; border: none; padding: 8px 4px;
}
QListWidget#transcript::item { padding: 0; border-radius: 0; background: transparent; }
QListWidget#transcript::item:hover { background: transparent; }
QLineEdit {
    background: #211f1e; border: 1px solid #34322f; border-radius: 8px;
    padding: 8px 11px; color: #e8e6e3; font-size: 13px;
}
QLineEdit:focus { border: 1px solid #55514c; }
QLabel#heading { font-size: 15px; font-weight: 600; padding: 12px 18px 2px; }
QLabel#subheading { color: #8b8985; font-size: 12px; padding: 0 18px 10px; }
"""

class ChatWindow(QMainWindow):
    """Conversations on the left, the transcript on the right."""

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.showing: int | None = None
        self.last_message_id = 0
        self.last_seen_overall = -1
        self.filter_text = ""

        self.setWindowTitle("Meow")
        self.setMinimumSize(QSize(760, 520))
        self.resize(980, 640)
        self.setStyleSheet(STYLE)
        self.setWindowIcon(icons.cat_icon())

        splitter = QSplitter(Qt.Horizontal)

        # --- left: search + conversation list ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 10, 8, 8)
        left_layout.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText("search everything said")
        self.search.textChanged.connect(self._on_search)
        left_layout.addWidget(self.search)

        self.conversation_list = QListWidget()
        self.conversation_list.setIconSize(QSize(20, 20))
        # Long titles are elided rather than given a scrollbar - a
        # horizontal scrollbar in a sidebar is never the right answer.
        self.conversation_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.conversation_list.setTextElideMode(Qt.ElideRight)
        self.conversation_list.setWordWrap(False)
        self.conversation_list.currentItemChanged.connect(self._on_pick)
        left_layout.addWidget(self.conversation_list)
        splitter.addWidget(left)

        # --- right: heading + transcript ---
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.heading = QLabel("nothing yet")
        self.heading.setObjectName("heading")
        self.subheading = QLabel("start talking and it will appear here")
        self.subheading.setObjectName("subheading")
        right_layout.addWidget(self.heading)
        right_layout.addWidget(self.subheading)

        # A list of painted bubbles, not a rich-text document. See
        # meow/chat/bubbles.py: Qt's rich text ignores border-radius and
        # stretches a coloured div to the full viewport width, so every
        # message came out as a flat bar edge to edge.
        self.transcript = QListWidget()
        self.transcript.setObjectName("transcript")
        self.transcript.setItemDelegate(MessageDelegate(self.transcript))
        self.transcript.setSelectionMode(QListWidget.NoSelection)
        self.transcript.clicked.connect(self._on_transcript_click)
        self.transcript.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.transcript.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.transcript.setSpacing(0)
        self.transcript.setWordWrap(True)
        right_layout.addWidget(self.transcript)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 720])
        self.setCentralWidget(splitter)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_MILLISECONDS)
        self.refresh(force=True)

    # --- data ------------------------------------------------------------

    def _conversations(self) -> list[Conversation]:
        everything = self.store.conversations()
        if not self.filter_text:
            return everything
        # Search returns messages; the sidebar lists conversations. Narrow to
        # the ones a match was found in, keeping their usual order.
        hits = {conversation.id for conversation, _
                in self.store.search(self.filter_text)}
        return [c for c in everything if c.id in hits]

    def refresh(self, force: bool = False) -> None:
        """Poll. Cheap when nothing changed, which is almost always."""
        newest = self.store.latest_message_id()
        changed = newest != self.last_seen_overall
        if not changed and not force:
            return
        self.last_seen_overall = newest

        self._rebuild_list()
        if self.showing is not None:
            self._append_new_messages()

    def _rebuild_list(self) -> None:
        conversations = self._conversations()
        # Rebuilt wholesale because the list is tens of rows, and reconciling
        # it in place would be more code than redrawing it. The selection is
        # restored by id so the user does not lose their place every poll.
        keep = self.showing
        self.conversation_list.blockSignals(True)
        self.conversation_list.clear()
        for conversation in conversations:
            label = conversation.title
            if conversation.live:
                label += "  ·"
            item = QListWidgetItem(
                icons.for_conversation(conversation.icon, conversation.live),
                label)
            item.setData(Qt.UserRole, conversation.id)
            self.conversation_list.addItem(item)
            if conversation.id == keep:
                self.conversation_list.setCurrentItem(item)
        self.conversation_list.blockSignals(False)

        if keep is None and conversations:
            self.show_conversation(conversations[0].id)

    def show_conversation(self, conversation_id: int) -> None:
        self.showing = conversation_id
        # Keep the sidebar selection in step, or the row highlight stays on
        # whatever was chosen before and the window looks broken.
        for row in range(self.conversation_list.count()):
            item = self.conversation_list.item(row)
            if int(item.data(Qt.UserRole)) == conversation_id:
                self.conversation_list.blockSignals(True)
                self.conversation_list.setCurrentItem(item)
                self.conversation_list.blockSignals(False)
                break
        self.last_message_id = 0
        self.transcript.clear()

        found = next((c for c in self.store.conversations()
                      if c.id == conversation_id), None)
        if found is not None:
            self.heading.setText(found.title)
            self.subheading.setText(
                ("running now" if found.live else "finished")
                + f"  ·  {found.kind}")
        self._append_new_messages()

    def _append_new_messages(self) -> None:
        if self.showing is None:
            return
        fresh = self.store.messages(self.showing, after_id=self.last_message_id)
        if not fresh:
            return
        bar = self.transcript.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 40
        for message in fresh:
            item = QListWidgetItem()
            item.setData(WHO, message.who)
            item.setData(TEXT, message.text)
            self.transcript.addItem(item)
            self.last_message_id = message.id
        if at_bottom:
            # Only follow if they were already at the bottom. Scrolling
            # someone away from what they are reading because a background
            # task said something is the most annoying thing a chat panel
            # can do.
            self.transcript.scrollToBottom()

    # --- events ----------------------------------------------------------

    def _on_pick(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        self.show_conversation(int(current.data(Qt.UserRole)))

    def _on_transcript_click(self, index) -> None:
        """Open a produced file when its row is clicked.

        Through the shell rather than a named application: the
        record holds .xlsx, .docx and .pptx, and whatever the user
        has set to open those is the right answer.
        """
        if is_ask(index):
            self._answer(index)
            return
        if not is_file(index):
            return
        path = file_path(index)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _answer(self, index) -> None:
        """Answer a question a task is stopped on.

        Written into the conversation as an ordinary message, because that is
        the only channel the two processes share - the task is blocked in the
        voice loop's process, polling this record for exactly this row.
        """
        option = QStyleOptionViewItem()
        option.rect = self.transcript.visualRect(index)
        point = self.transcript.viewport().mapFromGlobal(QCursor.pos())
        said = hit_yes(index, point, option)
        if said is None or self.showing is None:
            return
        self.store.add(self.showing, "answer", "yes" if said else "no")
        self.refresh(force=True)

    def _on_search(self, text: str) -> None:
        self.filter_text = text.strip()
        self._rebuild_list()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """Hide, never quit. The tray icon is how it comes back."""
        event.ignore()
        self.hide()


def main() -> None:
    application = QApplication(sys.argv)
    application.setQuitOnLastWindowClosed(False)
    QApplication.setFont(QFont("Segoe UI", 9))

    store = Store()
    window = ChatWindow(store)

    # Deep link. The voice loop uses this to open straight onto the task it
    # just handed over, rather than making the user find it in a list that
    # grows all session.
    for index, argument in enumerate(sys.argv):
        if argument == "--open" and index + 1 < len(sys.argv):
            try:
                window.show_conversation(int(sys.argv[index + 1]))
            except ValueError:
                pass

    def reveal(conversation_id: int | None = None) -> None:
        """Show the window, optionally on one particular conversation."""
        if conversation_id is not None:
            window.show_conversation(conversation_id)
        window.show()
        window.raise_()
        window.activateWindow()

    tray = QSystemTrayIcon(icons.cat_icon(icons.CHAT))
    tray.setToolTip("Meow - conversations")

    menu = QMenu()
    open_action = QAction("Open")
    quit_action = QAction("Quit")
    menu.addAction(open_action)
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)

    open_action.triggered.connect(lambda: reveal())
    quit_action.triggered.connect(application.quit)
    tray.activated.connect(
        lambda reason: reveal() if reason == QSystemTrayIcon.Trigger else None)
    tray.show()

    # One icon per running agent, alongside the cat. Driven by the window's
    # own timer so there is a single clock in this process.
    agents = AgentTrays(store, reveal, application.quit)
    agents.refresh()
    window.timer.timeout.connect(agents.refresh)
    application.aboutToQuit.connect(agents.close)

    if "--show" in sys.argv:
        reveal()

    sys.exit(application.exec())


if __name__ == "__main__":
    main()
