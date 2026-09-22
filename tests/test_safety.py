"""The properties that must hold however the code is rearranged.

These are not unit tests of a function; they are assertions about the SHAPE of
the project. A refactor that quietly added a send tool to the harness, or let
an approved draft be edited afterwards, would pass every other test in here.
"""

from __future__ import annotations

import pytest

from meow import tools
from meow.connectors.drafts import Outbox
from meow.connectors.reader import Reader
from meow.connectors.sender import SendRefused, Sender


def _harness():
    from meow.harness import Harness
    from meow.memory import Memory

    return Harness(confirm=lambda *a, **k: True, memory=Memory(),
                   outbox=Outbox())


def test_the_harness_holds_no_send_tool():
    """Invariant 2. The harness has the screen, which is private data AND
    untrusted content; one outbound tool closes the trifecta in a single move.
    """
    names = [t.name for t in tools.build(_harness())]
    outbound = [n for n in names
                if n.startswith(("send", "post", "publish", "deliver", "email_"))]
    assert outbound == [], f"outbound tool in the harness: {outbound}"


def test_drafting_is_not_sending():
    """Every tool that composes something addressed must say so in its name."""
    names = [t.name for t in tools.build(_harness())]
    assert "draft_reply" in names
    assert "draft_event" in names
    assert "draft_slack" in names


def test_an_unapproved_draft_cannot_be_sent():
    outbox = Outbox()
    draft = outbox.add(Reader.__new__(Reader).compose_reply(
        "someone@example.com", "subject", "body"))
    with pytest.raises(SendRefused):
        Sender(outbox).send(draft.id)


def test_an_approved_draft_cannot_be_edited():
    """This was live. A draft approved to a colleague was mutated afterwards
    and delivered to attacker@example.com - the user approved one message and
    a different one went out.
    """
    outbox = Outbox()
    draft = outbox.add(Reader.__new__(Reader).compose_reply(
        "colleague@example.com", "subject", "body"))
    outbox.approve(draft.id)
    with pytest.raises(TypeError):
        draft.payload["to"] = "attacker@example.com"
    assert outbox.get(draft.id).payload["to"] == "colleague@example.com"


def test_forcing_past_the_proxy_is_still_refused_at_send():
    """Defence in depth: approval is bound to a fingerprint of the contents,
    re-checked at send, so a future mutable path fails closed rather than
    delivering quietly.
    """
    outbox = Outbox()
    draft = outbox.add(Reader.__new__(Reader).compose_reply(
        "colleague@example.com", "subject", "body"))
    outbox.approve(draft.id)
    object.__setattr__(draft, "payload",
                       {"to": "attacker@example.com", "subject": "x",
                        "body": "y"})
    with pytest.raises(SendRefused):
        Sender(outbox).send(draft.id)


def test_every_tool_group_is_reachable():
    """Adding a module without listing it in ORDER would lose it silently."""
    harness = _harness()
    total = sum(len(module.build(harness)) for module in tools.ORDER)
    assert total == len(tools.build(harness)) == 30
