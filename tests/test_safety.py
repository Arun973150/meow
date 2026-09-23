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
    from meow.agent.harness import Harness
    from meow.agent.memory import Memory

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
    # Consistency, not a magic number. Pinning the count meant adding a tool
    # broke a SAFETY test for a reason that had nothing to do with safety,
    # which teaches people to edit the number rather than read the failure.
    assert total == len(tools.build(harness))
    assert total >= 30, f"tools disappeared: {total}"


def test_a_verdict_of_no_is_visible_to_the_planner():
    """The three-way verdict lived only in the sentence handed to the model,
    so a step the verifier had positively determined did NOT happen left
    last_error clear and nothing refused - marked done, plan continues.
    """
    from meow.desktop.actions import Outcome
    from meow.desktop.verify import Verdict
    from meow.tools.record import ToolRun

    harness = _harness()
    harness.runs.append(ToolRun("press_keys", "ctrl+t", Outcome(True, "sent")))

    harness.record_verdict(Verdict(True, "a tab appeared"))
    assert harness.denied_by_the_verifier() == ""

    harness.record_verdict(Verdict(False, "nothing changed"))
    assert "press_keys" in harness.denied_by_the_verifier()


def test_could_not_tell_is_not_treated_as_failure():
    """Clicking into a text box changes nothing observable, and that is not
    failure. A plan stopping on every unverifiable step would stop constantly,
    which is why unknown and no have to stay different.
    """
    from meow.desktop.actions import Outcome
    from meow.desktop.verify import Verdict
    from meow.tools.record import ToolRun

    harness = _harness()
    harness.runs.append(ToolRun("click_control", "Name", Outcome(True, "ok")))
    harness.record_verdict(Verdict(None, "nothing observable to check"))
    assert harness.denied_by_the_verifier() == ""
