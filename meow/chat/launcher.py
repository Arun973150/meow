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

import os
import subprocess
import sys
import time
from pathlib import Path

from meow.conversations import Store, database_path

WINDOW_SCRIPT = Path(__file__).resolve().parent / "window.py"

# How many characters of the first thing said become the conversation's name.
# Enough to recognise it in a sidebar, short enough not to be elided into
# uselessness.
TITLE_CHARACTERS = 48


def _window_already_running() -> int:
    """The pid of a chat window that is already up, or 0.

    Asked of the operating system rather than tracked in a file: a pid file
    written by a process that was killed says the window is running when it is
    not, and one deleted by a crash says the opposite. The question is "is
    there a python running window.py", and Windows can answer it directly.
    """
    ours = os.getpid()
    try:
        import subprocess as _subprocess

        finished = _subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" "
             "| Where-Object { $_.CommandLine -like '*chat*window.py*' } "
             "| Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(_subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:  # noqa: BLE001 - never block startup on this
        return 0

    for line in finished.stdout.splitlines():
        line = line.strip()
        if line.isdigit() and int(line) != ours:
            return int(line)
    return 0


class ChatPanel:
    """The window process, and the record it displays."""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.store: Store | None = None
        self.state = "not started"
        self.stale_closed = 0
        self.log_path: Path | None = None
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

        # The window is DETACHED, so it survives the cat dying - and a crash,
        # a Ctrl+C or a killed smoke test never reaches stop(). Every run then
        # spawned another one: 30 orphaned Qt processes were found live on this
        # machine, each holding a tray icon for a session that ended days ago.
        # The old ones are not merely wasteful, they are wrong - they show
        # conversations nothing is writing to any more.
        already = _window_already_running()
        if already:
            self.state = f"already open (pid {already})"
            return

        try:
            # No --show: it starts in the tray and waits to be clicked, which
            # is what was asked for. DETACHED so closing the terminal that
            # started the cat does not take the window with it.
            # The window's own output goes to a log rather than nowhere.
            # It runs detached with no console, so a traceback in it was
            # previously invisible - the only symptom being an icon that
            # never appeared.
            self.log_path = Path(database_path()).parent / "window.log"
            handle = open(self.log_path, "a", encoding="utf-8")
            environment = dict(os.environ)
            environment.setdefault("MEOW_TRAY_LOG", str(self.log_path))
            self.process = subprocess.Popen(
                [sys.executable, str(WINDOW_SCRIPT)],
                stdout=handle, stderr=handle, env=environment,
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

    def produced(self, conversation: int, path: str) -> None:
        """Record a file the agent wrote, so the window can open it.

        Stored as an ordinary message with `who` set to "file", rather than a
        table of its own. A produced file belongs in the conversation at the
        point it was produced - after the step that made it and before
        whatever came next - and a separate list would lose that ordering for
        no benefit.
        """
        if self.store is None or not conversation or not path:
            return
        try:
            self.store.add(conversation, "file", path)
        except Exception:  # noqa: BLE001
            pass

    def propose(self, conversation: int, draft) -> int | None:
        """Put a draft in the conversation, for someone to read and decide.

        The whole text, not a summary. A confirmation saying "send an email to
        your manager?" is not consent to the contents of an email nobody
        displayed - and the recipient is the part an injection tries to change,
        so it goes first.

        The id leads the message because approval is BY ID: what comes back has
        to identify one message rather than "the current draft".
        """
        if self.store is None or not conversation:
            return None
        try:
            body = draft.id + chr(10) + draft.describe()
            return self.store.add(conversation, "draft", body) or None
        except Exception:  # noqa: BLE001
            return None

    def ask(self, conversation: int, question: str) -> int | None:
        """Put a question in the conversation. Returns its message id, or None.

        None means there is nowhere to ask - no record, no window - and the
        caller must fall back to declining rather than blocking on an answer
        that can never arrive.
        """
        if self.store is None or not conversation or not question.strip():
            return None
        try:
            return self.store.add(conversation, "ask", question) or None
        except Exception:  # noqa: BLE001
            return None

    def wait_for_answer(self, conversation: int, after_id: int,
                        seconds: float) -> bool | None:
        """Block until somebody answers in the window, or give up.

        Polled rather than pushed, for the same reason the window polls: the
        two are separate processes and the record is the only thing they
        share. A second between checks is imperceptible against a question
        that has been sitting there waiting for a person.

        None means nobody answered. Silence is not consent.
        """
        if self.store is None or not conversation:
            return None
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            try:
                for message in self.store.messages(conversation,
                                                   after_id=after_id):
                    if message.who == "answer":
                        return message.text.strip().lower().startswith("y")
            except Exception:  # noqa: BLE001
                return None
            time.sleep(1.0)
        return None

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
