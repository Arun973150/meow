"""Every conversation, kept - so you can go back and read it.

`meow/memory.py` is working memory: ten turns, in process, gone when the app
closes. That is correct for what it does. It is not a record, and the user
asked for one - somewhere the chats accumulate and stay reachable afterwards.

So: SQLite, in `Documents/Meow`, beside the documents and the recipes.

**SQLite rather than files**, because the question that will be asked of this
is "what did the research agent find last Tuesday", and that is a query. A
folder of JSON files answers it by reading every file. It is also in the
standard library, which matters on a CPU-only machine where every dependency
has already had to justify itself.

**Written from several threads and read from another process.** The voice loop
appends, background tasks append, and the chat window - a separate process,
because Qt wants the main thread and the cat's overlay already has it - reads.
SQLite handles that if it is asked properly: WAL so a reader never blocks a
writer, one connection per thread, and a busy timeout so a concurrent write
waits rather than raising.

**Nothing here is deleted automatically.** A record that quietly discards old
entries is not a record. Pruning, if it is ever wanted, is something the user
asks for.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'chat',
    icon        TEXT    NOT NULL DEFAULT 'cat',
    started_at  REAL    NOT NULL,
    ended_at    REAL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    who             TEXT    NOT NULL,
    text            TEXT    NOT NULL,
    at              REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS messages_by_conversation
    ON messages (conversation_id, id);
"""


def database_path() -> Path:
    folder = (Path(os.environ.get("USERPROFILE", Path.home()))
              / "Documents" / "Meow")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "conversations.db"


@dataclass(frozen=True)
class Message:
    id: int
    who: str          # "user", "meow", or an agent's name
    text: str
    at: float


@dataclass(frozen=True)
class Conversation:
    id: int
    title: str
    kind: str         # "chat" for the voice loop, "task" for handed-over work
    icon: str         # which icon the window shows for it
    started_at: float
    ended_at: float | None = None

    @property
    def live(self) -> bool:
        return self.ended_at is None


class Store:
    """The record. Safe to use from several threads and several processes."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or database_path()
        # One connection per thread. Sharing one across threads is the classic
        # way to get "SQLite objects created in a thread can only be used in
        # that same thread" from a background task, hours in.
        self._local = threading.local()
        self._prepare()

    def _connection(self) -> sqlite3.Connection:
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            return existing
        # timeout, not a retry loop: a concurrent writer should make this WAIT,
        # not raise "database is locked" into the middle of a voice turn.
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        # WAL so the chat window can read while the voice loop is writing.
        # Without it a reader and a writer block each other and the window
        # freezes exactly when there is something new to show.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        self._local.connection = connection
        return connection

    def _prepare(self) -> None:
        connection = self._connection()
        connection.executescript(SCHEMA)
        connection.commit()

    # --- writing ---------------------------------------------------------

    def start(self, title: str, kind: str = "chat", icon: str = "cat") -> int:
        """Begin a conversation. Returns its id."""
        connection = self._connection()
        cursor = connection.execute(
            "INSERT INTO conversations (title, kind, icon, started_at) "
            "VALUES (?, ?, ?, ?)",
            (title.strip() or "untitled", kind, icon, time.time()))
        connection.commit()
        return int(cursor.lastrowid)

    def add(self, conversation_id: int, who: str, text: str) -> int:
        """Append one message. Empty text is ignored rather than stored."""
        cleaned = " ".join(str(text).split())
        if not cleaned:
            return 0
        connection = self._connection()
        cursor = connection.execute(
            "INSERT INTO messages (conversation_id, who, text, at) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, who, cleaned, time.time()))
        connection.commit()
        return int(cursor.lastrowid)

    def finish(self, conversation_id: int) -> None:
        """Mark it ended. The conversation stays readable forever."""
        connection = self._connection()
        connection.execute(
            "UPDATE conversations SET ended_at = ? WHERE id = ? "
            "AND ended_at IS NULL",
            (time.time(), conversation_id))
        connection.commit()

    def close_stale(self) -> int:
        """End every conversation still marked live. Returns how many.

        Called once by the voice loop at startup, because nothing from a
        previous process is running any more: a crash, a Ctrl+C or a machine
        that went to sleep leaves conversations open forever, and the window
        would then show an agent icon for work that stopped days ago and can
        never finish.

        The voice loop does this, not the window - the window may well start
        while the loop is mid-task, and closing a live conversation out from
        under a running agent is the opposite of the bug being fixed.
        """
        connection = self._connection()
        cursor = connection.execute(
            "UPDATE conversations SET ended_at = ? WHERE ended_at IS NULL",
            (time.time(),))
        connection.commit()
        return int(cursor.rowcount or 0)

    def rename(self, conversation_id: int, title: str) -> None:
        connection = self._connection()
        connection.execute("UPDATE conversations SET title = ? WHERE id = ?",
                           (title.strip() or "untitled", conversation_id))
        connection.commit()

    # --- reading ---------------------------------------------------------

    def conversations(self, limit: int = 200) -> list[Conversation]:
        """Newest first, which is the order a sidebar wants them in."""
        rows = self._connection().execute(
            "SELECT * FROM conversations ORDER BY started_at DESC LIMIT ?",
            (limit,)).fetchall()
        return [Conversation(id=row["id"], title=row["title"],
                             kind=row["kind"], icon=row["icon"],
                             started_at=row["started_at"],
                             ended_at=row["ended_at"]) for row in rows]

    def messages(self, conversation_id: int, after_id: int = 0) -> list[Message]:
        """In order. `after_id` returns only what is new since that message.

        The window polls with the last id it has rather than re-reading the
        whole conversation, so an hour-long session does not re-render itself
        several times a second.
        """
        rows = self._connection().execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND id > ? "
            "ORDER BY id", (conversation_id, after_id)).fetchall()
        return [Message(id=row["id"], who=row["who"], text=row["text"],
                        at=row["at"]) for row in rows]

    def search(self, text: str, limit: int = 50) -> list[tuple[Conversation, Message]]:
        """Find a message, and say which conversation it was in.

        The point of keeping any of this: "what did it say about solar panels"
        has to be answerable weeks later.
        """
        needle = f"%{' '.join(str(text).split())}%"
        # The TITLE matches too. "what did it say about solar panels" should
        # find a conversation called "research solar panel costs" even when no
        # individual message repeats the word - which is the normal case, since
        # the title is the subject and the messages are progress reports.
        rows = self._connection().execute(
            "SELECT m.*, c.title, c.kind, c.icon, c.started_at, c.ended_at "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.text LIKE ? OR c.title LIKE ? "
            "ORDER BY m.id DESC LIMIT ?",
            (needle, needle, limit)).fetchall()
        found = []
        for row in rows:
            found.append((
                Conversation(id=row["conversation_id"], title=row["title"],
                             kind=row["kind"], icon=row["icon"],
                             started_at=row["started_at"],
                             ended_at=row["ended_at"]),
                Message(id=row["id"], who=row["who"], text=row["text"],
                        at=row["at"]),
            ))
        return found

    def latest_message_id(self) -> int:
        """Cheap change detector for a poller - one indexed row."""
        row = self._connection().execute(
            "SELECT MAX(id) AS newest FROM messages").fetchone()
        return int(row["newest"] or 0)
