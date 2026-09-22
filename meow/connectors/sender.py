"""SENDER - sends what a person approved, and cannot read anything.

The third leg of the trifecta lives here, alone. It has no tool that fetches,
searches, lists or reads, so there is nothing for it to exfiltrate even if
something persuaded it to try: it can only send a draft it was handed, to the
address written on that draft.

**It takes a draft id, not a description.** `send(draft_id)` rather than
`send(to, subject, body)`, and the difference is the whole safety property. A
sender that accepts arguments can be called with arguments assembled from
anything, including a page or an email; a sender that accepts an id can only
send something that already exists in the outbox and has already been approved
by a person looking at the exact recipient and the exact body.

**It is not a tool the model holds.** The harness has the desktop and the
screen, which is private data and untrusted content, and giving it a send tool
would close the trifecta in one move. The app calls this directly, after the
user has said yes, and the model is not in that path at all.

There is no `send_now`, no `send_without_confirmation`, and no flag that turns
the check off. Adding one would be the whole of Phase 4 undone.
"""

from __future__ import annotations

from .composio import Composio
from .connect import Connector, toolkit_for
from .drafts import Draft, Outbox


def _toolkit_for_kind(kind: str) -> str:
    """Which service delivers this kind of draft."""
    return {"email": "gmail",
            "calendar_event": "googlecalendar",
            "slack_message": "slack"}.get(kind, kind)


class SendRefused(Exception):
    """A send was attempted for something nobody approved."""


class Sender:
    """Delivers approved drafts. Holds nothing that could read."""

    def __init__(self, outbox: Outbox, composio: Composio | None = None,
                 announce=None) -> None:
        self.outbox = outbox
        self.composio = composio or Composio()
        # A draft approved for a service that was never connected
        # should open the login, not fail after the user has already
        # said yes. Asking AFTER approval is the right order: nothing
        # is sent until both the person and the service have agreed.
        self.connector = Connector(self.composio)
        self.announce = announce
        # Every send, for afterwards. A send is the one thing here that cannot
        # be undone, so what went out and when is worth keeping even though
        # nothing reads it yet.
        self.sent: list[tuple[str, str]] = []

    @property
    def configured(self) -> bool:
        return self.composio.configured

    def send(self, draft_id: str) -> str:
        """Send an approved draft. Refuses anything else.

        Claimed rather than read: claiming moves the draft out of APPROVED, so
        the same approval cannot send twice. One "yes" is one message.
        """
        draft = self.outbox.claim(draft_id)
        if draft is None:
            state = self.outbox.state(draft_id)
            if state is None:
                raise SendRefused(f"there is no draft {draft_id}")
            raise SendRefused(
                f"draft {draft_id} is {state}, not approved - a draft can only "
                f"be sent once, and only after somebody has seen exactly what "
                f"it says")

        try:
            outcome = self._deliver(draft)
            if not outcome.ok and "not connected yet" in outcome.error:
                connection = self.connector.ensure(
                    _toolkit_for_kind(draft.kind),
                    announce=self.announce)
                if connection.ok:
                    outcome = self._deliver(draft)
        except Exception as error:  # noqa: BLE001
            self.outbox.failed(draft_id)
            return f"Could not send it: {type(error).__name__}: {error}"

        if not outcome.ok:
            self.outbox.failed(draft_id)
            return f"Could not send it: {outcome.error}"

        self.sent.append((draft_id, draft.spoken()))
        return f"Sent {draft.spoken()}."

    def _deliver(self, draft: Draft):
        """The one place a request actually leaves the machine."""
        if draft.kind == "email":
            return self.composio.execute("GMAIL_SEND_EMAIL", {
                "recipient_email": draft.payload.get("to"),
                "subject": draft.payload.get("subject"),
                "body": draft.payload.get("body"),
            })
        if draft.kind == "calendar_event":
            return self.composio.execute("GOOGLECALENDAR_CREATE_EVENT", {
                "summary": draft.payload.get("title"),
                "start_datetime": draft.payload.get("start"),
                "event_duration_hour": draft.payload.get("hours", 1),
                "attendees": draft.payload.get("attendees") or [],
            })
        if draft.kind == "slack_message":
            return self.composio.execute("SLACK_SENDS_A_MESSAGE_TO_A_SLACK_CHANNEL", {
                "channel": draft.payload.get("channel"),
                "text": draft.payload.get("text"),
            })
        raise SendRefused(f"nothing here knows how to send a {draft.kind!r}")
