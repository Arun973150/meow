"""The handoff. Nothing leaves this machine without passing through here.

A draft is what the reader produces and the sender consumes, and the only way
between them is a person looking at it and saying yes.

**Why a whole object for it.** The obvious shape is a confirmer - ask "send
this?" and act on the answer - and that is not enough. Consent means knowing
what is being consented to, so the thing being approved has to be the thing
that gets sent, exactly, with nothing added between the question and the act.
A draft is frozen, carries its own identity, and is approved BY ID: the sender
is handed an approval for draft 7 and sends draft 7, not "whatever the current
draft is".

**Approval is one-shot.** Once sent, a draft cannot be sent again. Without
that, "yes" to one message is a standing yes, and a loop that re-reads its own
outbox would send the same mail until something stopped it.

**What the user sees is not a summary.** `describe()` is the exact recipient
and the exact body, truncated only at a length nobody would read past. A
confirmation showing "send an email to your manager?" is not consent to the
contents of an email nobody displayed.
"""

from __future__ import annotations

import re

import copy
import hashlib
import json
import threading
import time
import types
import uuid
from dataclasses import dataclass, field

# Enough of a body to decide on. Longer than this and it is read in the chat
# window rather than a confirmation line, but the full text is always what is
# sent - this only affects how much is quoted back.
PREVIEW_CHARACTERS = 600


class DraftState:
    WAITING = "waiting"
    APPROVED = "approved"
    REFUSED = "refused"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True)
class Draft:
    """Something composed but not sent. Frozen: what is approved is what goes.

    `payload` is the structured arguments the sender will use - recipient,
    subject, body - rather than a sentence for a model to interpret. The sender
    takes structured input only, and this is that structure.
    """

    kind: str                       # "email", "calendar_event", "slack_message"
    payload: dict
    summary: str = ""
    source: str = ""                # what it was composed from, for the record
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    made_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        """Make the payload genuinely unchangeable.

        `frozen=True` stops `draft.payload = ...` and does nothing at all about
        `draft.payload["to"] = ...`. That gap was live and exploitable: an
        attack test approved a draft addressed to a colleague, mutated the dict
        afterwards, and the sender delivered it to attacker@example.com. The
        user had approved one message and a different one went out, which is
        the precise failure this whole phase exists to prevent.

        So the payload is deep-copied on the way in and wrapped read-only. A
        caller may hold a reference and may write to their own copy; what the
        draft carries is what was shown.
        """
        frozen = types.MappingProxyType(copy.deepcopy(dict(self.payload)))
        object.__setattr__(self, "payload", frozen)

    @property
    def fingerprint(self) -> str:
        """What this draft says, as a hash. Approval is bound to it.

        Belt as well as braces. The read-only payload is what makes tampering
        impossible; this is what makes tampering DETECTED, so a future change
        that reintroduces a mutable path fails loudly at the send rather than
        quietly delivering to the wrong address.
        """
        material = json.dumps({"kind": self.kind,
                               "payload": dict(self.payload)},
                              sort_keys=True, default=str)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def describe(self) -> str:
        """Exactly what will happen, for someone deciding whether to allow it.

        Recipient first, because that is the part that matters and the part an
        injection tries to change. A body that is wrong is embarrassing; a
        recipient that is wrong is the incident.
        """
        if self.kind == "email":
            body = str(self.payload.get("body", ""))
            if len(body) > PREVIEW_CHARACTERS:
                body = body[:PREVIEW_CHARACTERS] + "..."
            return (f"Send an email\n"
                    f"  to:      {self.payload.get('to', '(nobody)')}\n"
                    f"  subject: {self.payload.get('subject', '(none)')}\n"
                    f"  body:\n{body}")
        if self.kind == "calendar_event":
            return (f"Create a calendar event\n"
                    f"  title: {self.payload.get('title', '(none)')}\n"
                    f"  when:  {self.payload.get('start', '?')} "
                    f"to {self.payload.get('end', '?')}\n"
                    f"  with:  {self.payload.get('attendees', 'nobody')}")
        if self.kind == "slack_message":
            return (f"Post to Slack\n"
                    f"  channel: {self.payload.get('channel', '(none)')}\n"
                    f"  text:\n{str(self.payload.get('text', ''))[:PREVIEW_CHARACTERS]}")
        return f"{self.kind}: {self.payload}"

    def spoken(self) -> str:
        """One line for the ear. Never the body - that is read, not heard."""
        if self.kind == "email":
            return (f"an email to {self.payload.get('to', 'nobody')}, "
                    f"about {self.payload.get('subject', 'nothing')}")
        if self.kind == "calendar_event":
            return f"an event called {self.payload.get('title', 'nothing')}"
        if self.kind == "slack_message":
            return f"a message in {self.payload.get('channel', 'nowhere')}"
        return self.kind


class Outbox:
    """Drafts waiting on a person, and what became of them.

    Held rather than passed around, so that "approve draft 7" means the same
    thing to the voice loop, the chat window and the sender - three places that
    must agree about exactly one message.
    """

    def __init__(self) -> None:
        self._drafts: dict[str, Draft] = {}
        self._state: dict[str, str] = {}
        # What each draft said WHEN IT WAS APPROVED. Checked again at send, so
        # an approval cannot be carried over to different contents.
        self._approved_as: dict[str, str] = {}
        self._lock = threading.Lock()

    def add(self, draft: Draft) -> Draft:
        with self._lock:
            self._drafts[draft.id] = draft
            self._state[draft.id] = DraftState.WAITING
        return draft

    def get(self, draft_id: str) -> Draft | None:
        with self._lock:
            return self._drafts.get(draft_id)

    def state(self, draft_id: str) -> str | None:
        with self._lock:
            return self._state.get(draft_id)

    def waiting(self) -> list[Draft]:
        with self._lock:
            return [draft for draft_id, draft in self._drafts.items()
                    if self._state.get(draft_id) == DraftState.WAITING]

    def newest_waiting(self) -> Draft | None:
        waiting = self.waiting()
        return max(waiting, key=lambda d: d.made_at) if waiting else None

    def approve(self, draft_id: str) -> Draft | None:
        """Mark a draft approved, ONCE. Returns it, or None if it cannot be.

        Returning None rather than raising, and only from WAITING: a draft that
        is already sent, already refused, or does not exist must not become
        sendable because something asked twice.
        """
        with self._lock:
            if self._state.get(draft_id) != DraftState.WAITING:
                return None
            draft = self._drafts.get(draft_id)
            if draft is None:
                return None
            self._state[draft_id] = DraftState.APPROVED
            # Bound to the contents, not just the id. "Yes" was said about
            # particular words going to a particular address.
            self._approved_as[draft_id] = draft.fingerprint
            return draft

    def refuse(self, draft_id: str) -> None:
        with self._lock:
            if self._state.get(draft_id) == DraftState.WAITING:
                self._state[draft_id] = DraftState.REFUSED

    def claim(self, draft_id: str) -> Draft | None:
        """Take an APPROVED draft to send it. Returns None if it is not.

        The one-shot gate, and the reason approve and claim are separate: a
        draft can only be claimed from APPROVED, and claiming moves it out of
        that state, so the same approval cannot send twice. Without it, one
        "yes" is a standing yes and anything that re-read the outbox would
        send the same mail until something stopped it.
        """
        with self._lock:
            if self._state.get(draft_id) != DraftState.APPROVED:
                return None
            draft = self._drafts.get(draft_id)
            if draft is None:
                return None
            if draft.fingerprint != self._approved_as.get(draft_id):
                # The contents changed between approval and send. Impossible
                # through the current API, which is the point: if it ever
                # becomes possible again this fails closed instead of
                # delivering to whoever the new address is.
                self._state[draft_id] = DraftState.REFUSED
                return None
            self._state[draft_id] = DraftState.SENT
            return draft

    def failed(self, draft_id: str) -> None:
        with self._lock:
            self._state[draft_id] = DraftState.FAILED


# A spoken email address NEVER arrives as a valid email address. AssemblyAI
# hears "gowda arun zero three two at gmail dot com" and writes
# "Gauda Arun 032 gmail.com" - spaces through the middle, the @ gone entirely,
# sometimes "at" and "dot" left as words. Passed straight through, the draft
# held an address that could not exist, and the failure only surfaced at send:
# "invalid email format passed: gowda arun 032 gmail.com".
#
# The loop that follows is the real damage. The cat asked for "the address
# with no spaces", which is not something the user can say - the spaces come
# from the transcriber, not from them - so the same request came back four
# times and the mail was never sent. Whatever cannot be fixed by speaking more
# clearly has to be fixed here.
_ADDRESS = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")

# Said aloud, and written as words rather than symbols.
_SPOKEN_SYMBOLS = ((" at the rate of ", "@"), (" at the rate ", "@"),
                   (" at sign ", "@"), (" at ", "@"),
                   (" dot ", "."), (" point ", "."), (" period ", "."))


def spoken_email(text: str) -> str:
    """An email address as a microphone delivers it, or "" if it cannot be.

    Returning "" rather than a guess: an address that is wrong in a way
    nobody notices is worse than one that visibly failed, because the mail
    goes somewhere and the sender believes it arrived.
    """
    spoken = f" {str(text).lower().strip()} "
    spoken = spoken.strip(" .,!?")
    spoken = f" {spoken} "
    for said, symbol in _SPOKEN_SYMBOLS:
        spoken = spoken.replace(said, symbol)
    spoken = spoken.strip()

    if "@" in spoken:
        # Split on the LAST one: "pat at gmail.com" becomes "pat@gmail.com"
        # and a name containing "at" does not take the domain with it.
        local, _, domain = spoken.rpartition("@")
    else:
        # No @ survived the transcription at all, which is the common case.
        # The domain is the last thing with a dot in it; everything before it
        # is the name, spaces and all.
        parts = spoken.split()
        domain = ""
        for index in range(len(parts) - 1, -1, -1):
            if "." in parts[index]:
                domain = parts[index]
                local = " ".join(parts[:index])
                break
        if not domain:
            return ""

    local = "".join(local.split())
    domain = "".join(domain.split())
    address = f"{local}@{domain}"
    return address if _ADDRESS.match(address) else ""
