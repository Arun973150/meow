"""Starting the chat window, and writing into it - from the voice loop's side.

The window is a separate process (see `meow/chat/window.py` for why), so the
voice loop needs two things: a way to start it, and a way to record into the
store it reads. Both are here so that `scripts/meow.py` deals with one object
rather than a subprocess handle and a database.

**Everything here fails quietly.** The window is a convenience and the cat is
not. A Qt that will not start, a tray that is unavailable on this desktop, a
database on a full disk - none of those are reasons for the voice loop to stop
working, so every call is wrapped and the failure is reported once in the
banner rather than raised into a turn.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from meow.conversations import Store

WINDOW_SCRIPT = Path(__file__).resolve().parent / "window.py"

# How many characters of the first thing said become the conversation's name.
# Enough to recognise it in a sidebar, short enough not to be elided into
# uselessness.
TITLE_CHARACTERS = 48


class ChatPanel:
    """The window process, and the record it displays."""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.store: Store | None = None
        self.state = "not started"
        self.stale_closed = 0
        self._named: set[int] = set()

    def start(self) -> None:
        """Open the store and launch the window, hidden in the tray."""
        try:
            self.store = Store()
            # Anything still marked live belongs to a process that is gone.
            # Left alone, a crash yesterday puts an agent icon in the tray
            # today for work that can never finish.
            self.stale_closed = self.store.close_stale()
        except Exception as error:  # noqa: BLE001
            self.state = f"no record ({type(error).__name__})"
            return

        try:
            # No --show: it starts in the tray and waits to be clicked, which
            # is what was asked for. DETACHED so closing the terminal that
            # started the cat does not take the window with it.
            self.process = subprocess.Popen(
                [sys.executable, str(WINDOW_SCRIPT)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.state = "in the tray - click the cat to open it"
        except Exception as error:  # noqa: BLE001
            self.state = f"recording only ({type(error).__name__})"

    # --- recording -------------------------------------------------------

    def begin(self, title: str, kind: str = "chat", icon: str = "cat") -> int:
        if self.store is None:
            return 0
        try:
            return self.store.start(title[:TITLE_CHARACTERS], kind=kind,
                                    icon=icon)
        except Exception:  # noqa: BLE001
            return 0

    def say(self, conversation: int, who: str, text: str) -> None:
        if self.store is None or not conversation:
            return
        try:
            self.store.add(conversation, who, text)
        except Exception:  # noqa: BLE001 - a lost line is not worth a crash
            pass

    def first_words(self, conversation: int, said: str) -> bool:
        """Name a conversation after the first thing said in it.

        A sidebar of rows all called "session" is a sidebar nobody reads. The
        first sentence is what the conversation turned out to be about, and it
        is available for free. Renamed once; later turns leave it alone, so the
        name stays the thing that started it.
        """
        if self.store is None or not conversation or conversation in self._named:
            return False
        self._named.add(conversation)
        try:
            self.store.rename(conversation, said[:TITLE_CHARACTERS])
            return True
        except Exception:  # noqa: BLE001
            return False

    def end(self, conversation: int) -> None:
        if self.store is None or not conversation:
            return
        try:
            self.store.finish(conversation)
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        """Close the window when the cat quits.

        Terminated rather than left running: the window with nothing writing
        into it is a window showing a conversation that has stopped, with no
        way to tell from looking at it.
        """
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:  # noqa: BLE001
                pass
