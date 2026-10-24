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


# --- a confirmation that becomes furniture is worse than none ---------------
#
# Live, "open spotify in a new chrome tab here" asked FIVE times: ctrl+t,
# ctrl+l, ctrl+t, ctrl+l, ctrl+l. The user answered yes to every one. That is
# precisely how somebody stops reading the question, which is the failure
# invariant 6 exists to prevent - and not one of those keystrokes could lose
# a character of anybody's work.


@pytest.mark.parametrize("chord", [
    "ctrl+t", "ctrl+l", "ctrl+a", "tab", "escape", "ctrl+f", "ctrl+z",
    "f5", "home", "end",
])
def test_a_keystroke_that_moves_somewhere_does_not_ask(chord):
    from meow.agent.risk import Decision, judge

    decision = judge("press_keys", chord, "open spotify in a new chrome tab")
    assert decision.decision is Decision.PROCEED, chord


@pytest.mark.parametrize("chord", [
    "ctrl+w", "alt+f4", "shift+delete", "ctrl+shift+delete", "ctrl+q",
])
def test_a_keystroke_that_loses_work_always_asks(chord):
    """Not overridable by clarity. "Close this" is clear and that is not a
    reason to skip the question.
    """
    from meow.agent.risk import Decision, judge

    decision = judge("press_keys", chord, f"press {chord} for me")
    assert decision.decision is Decision.ASK, chord


@pytest.mark.parametrize("said", [
    "search youtube.com",
    "google the weather in delhi",
    "go to spotify dot com",
    "find campus x on youtube",
])
def test_enter_proceeds_when_the_sentence_asked_for_it(said):
    """"Search youtube.com" IS the instruction to press Enter. Asking
    separately repeats their own sentence back at them.
    """
    from meow.agent.risk import Decision, judge

    assert judge("press_keys", "enter", said).decision is Decision.PROCEED


@pytest.mark.parametrize("said", [
    "say hi to the team",
    "reply to arun",
    "post that in the channel",
    "message him back",
])
def test_enter_still_asks_where_it_might_SEND(said):
    """In an address bar Enter navigates; in Slack or a mail client it sends,
    which is the one thing this project will not do without a person looking
    at it. The safe list deliberately does not contain Enter.
    """
    from meow.agent.risk import Decision, judge

    assert judge("press_keys", "enter", said).decision is Decision.ASK, said


def test_a_dangerous_chord_is_not_rescued_by_the_sentence():
    """The submitting words must not become a way round the dangerous set."""
    from meow.agent.risk import Decision, judge

    assert judge("press_keys", "shift+delete",
                 "search for the file and delete it").decision is Decision.ASK


# --- never into a password field --------------------------------------------
#
# `NOT_TEXT_FIELDS` is a deny-list of control TYPES and a password box is an
# ordinary EditControl, so it passed every check in the module and the
# keystrokes went in. Worse, the confirmation is SPOKEN and the Outcome is
# written to conversations.db, so the secret was read aloud and then kept.


def _typed(secret, role="EditControl", text="hunter2", name="a box"):
    """type_text against a fake focused control.

    `_send` is the one call that reaches Windows, and it is neutered here -
    a test that types for real types into whatever window happens to have
    focus, which is the running editor.
    """
    from meow.desktop import actions

    real_focus = actions._focused_control_description
    real_send = actions._send
    real_paste = actions.paste_text
    pressed = []
    actions._focused_control_description = lambda: (role, name, secret)
    actions._send = lambda *a, **k: pressed.append(a) or 1
    actions.paste_text = lambda value: pressed.append(("paste", value)) or False
    try:
        asked = []
        outcome = actions.type_text(
            text, confirm=lambda question: asked.append(question) or True)
        return outcome, asked, pressed
    finally:
        actions._focused_control_description = real_focus
        actions._send = real_send
        actions.paste_text = real_paste


def test_a_password_field_is_refused():
    outcome, asked, pressed = _typed(secret=True)

    assert outcome.ok is False
    assert "password field" in outcome.detail
    assert pressed == [], "not one keystroke"


def test_the_secret_is_never_spoken_or_written_DOWN():
    """The refusal comes BEFORE the preview is built, so a password is never
    previewed, read aloud or put in the conversation store."""
    outcome, asked, _pressed = _typed(secret=True, text="correct horse")

    assert asked == [], "nothing was asked, so nothing was said out loud"
    assert "correct horse" not in outcome.detail
    assert "correct horse" not in str(outcome)


def test_an_ordinary_text_field_is_untouched_by_the_check():
    outcome, asked, pressed = _typed(secret=False, text="hello")

    assert asked, "an ordinary field still confirms"
    assert "password" not in outcome.detail.lower()
    assert pressed, "and the keystrokes go out"


def test_IsPassword_is_a_PROPERTY_not_a_method():
    """Had it been a method, `element.IsPassword` would be a bound method -
    truthy - and every field on the machine would read as a password,
    refusing all typing everywhere. Checked against the installed library
    rather than assumed."""
    import uiautomation

    attribute = uiautomation.Control.__dict__.get("IsPassword")
    assert isinstance(attribute, property), type(attribute)


def test_a_field_that_will_not_say_is_treated_as_secret():
    """Fail closed. Refusing to type is a visible failure the user can
    report; typing a password into a field is invisible, already submitted,
    and in the conversation store."""
    import inspect

    from meow.desktop import actions

    source = inspect.getsource(actions._focused_control_description)
    body = source[source.index("element.IsPassword"):]
    assert "secret = True" in body


# --- the plan is made with the recent turns; a step still is not ------------
#
# `_plan` invoked with the prompt and the goal only, so "do it" ten seconds
# after describing a job planned from the words "do it".


def test_the_plan_sees_the_recent_turns():
    import inspect

    from meow.agent import planner as planner_module

    plan = inspect.getsource(planner_module.Planner._plan)
    assert "self._recently()" in plan
    assert "not an instruction" in plan, "context, labelled as context"


def test_a_STEP_still_gets_no_transcript():
    """Different question, and the distinction is the shape of the file: a
    step is a standalone instruction against the screen as it is then."""
    import inspect

    from meow.agent import planner as planner_module

    step = inspect.getsource(planner_module.Planner._step)
    assert "_recently" not in step


def test_the_planner_survives_a_harness_with_no_memory():
    from meow.agent.planner import Planner

    nothing = type("Bare", (), {})()
    assert Planner._recently(type("P", (), {"harness": nothing})()) == ""


# --- the prompt rules out of the teardown ----------------------------------

def test_the_planner_is_told_it_cannot_see_the_screen():
    from meow.agent.planner import PLANNER_PROMPT

    assert "NO SCREENSHOT IS ATTACHED" in PLANNER_PROMPT


def test_an_uncertain_action_is_looked_at_rather_than_repeated():
    from meow.agent.harness import SYSTEM_PROMPT

    assert "DO NOT REDO" in SYSTEM_PROMPT
    assert "sends a thing" in SYSTEM_PROMPT


def test_the_voice_does_not_narrate_what_it_is_about_to_do():
    from meow.agent.harness import STYLE_REMINDER

    assert "never name a tool" in STYLE_REMINDER
    assert "five words at most" in STYLE_REMINDER
    assert "not to recite" in STYLE_REMINDER
