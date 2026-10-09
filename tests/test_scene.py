"""Reading an application's own account of its state.

Pure: a fake socket in, a prompt block out. No Blender, no network.

The security property is the point of most of these. The add-on's
`execute_code` is a bare `exec(code, {"bpy": bpy})` with no sandbox and no
authentication, so the only thing that makes it safe to use at all is that
nothing outside `scene.py` can influence the string. That is checked here by
inspection of the module rather than by trusting a comment.
"""

from __future__ import annotations

import json

import pytest

from meow.desktop import scene
from meow.desktop.scene import Scene


class FakeSocket:
    """One request, one reply, shaped like the add-on's."""

    def __init__(self, replies, fail=None):
        self.replies = list(replies)
        self.fail = fail
        self.sent = []
        self.timeouts = []
        self.closed = False

    def settimeout(self, seconds):
        self.timeouts.append(seconds)

    def connect(self, where):
        if self.fail is not None:
            raise self.fail

    def sendall(self, payload):
        self.sent.append(json.loads(payload.decode("utf-8")))

    def recv(self, size):
        if not self.replies:
            return b""
        return json.dumps(self.replies.pop(0)).encode("utf-8")

    def close(self):
        self.closed = True


def wired(monkeypatch, replies=None, fail=None):
    """A Scene whose socket is a fake, and the fake, so it can be inspected."""
    state = {"mode": "EDIT_MESH", "active": "Cube", "type": "MESH",
             "selected": ["Cube"], "modifiers": ["Subdivision"],
             "materials": ["Material"], "frame": 1, "editor": "VIEW_3D"}
    if replies is None:
        replies = [{"status": "success",
                    "result": {"executed": True,
                               "result": json.dumps(state) + "\n"}}]
    fake = FakeSocket(replies, fail)
    monkeypatch.setattr(scene.socket, "socket", lambda *a, **k: fake)
    return Scene(), fake


# --- the security property ---------------------------------------------------


def test_the_questions_are_CONSTANTS_with_no_way_to_pass_code():
    """`execute_code` runs whatever it is sent. The only safe property is that
    the string is unreachable from outside, not that it is validated.
    """
    import inspect

    source = inspect.getsource(scene)
    # The constant exists and is a plain string literal assignment.
    assert isinstance(scene.WHAT_IS_ON, str)
    assert "exec" not in scene.WHAT_IS_ON
    # Nothing formats, concatenates or interpolates into the code argument.
    for forbidden in ("code=code", "code=f\"", "code=f'", ".format(",
                      "code=%", "code=' +", 'code=" +'):
        assert forbidden not in source, f"{forbidden!r} reaches the socket"


def test_look_sends_exactly_the_constant(monkeypatch):
    live, fake = wired(monkeypatch)
    live.look()
    assert len(fake.sent) == 1
    assert fake.sent[0]["type"] == "execute_code"
    assert fake.sent[0]["params"]["code"] == scene.WHAT_IS_ON


def test_every_question_only_READS(monkeypatch):
    """A tool that could move the user's geometry belongs behind the
    confirmation gate, and none is offered here.
    """
    for line in scene.WHAT_IS_ON.splitlines():
        stripped = line.strip()
        # bpy.ops.* is how anything in Blender is DONE. Not one call.
        assert "bpy.ops" not in stripped, stripped
        assert not stripped.startswith("del "), stripped
    # The only side effect is printing.
    assert "print(json.dumps(" in scene.WHAT_IS_ON


# --- reading it --------------------------------------------------------------


def test_the_mode_comes_back_as_the_KEYMAP_CONTEXT(monkeypatch):
    """Blender says EDIT_MESH; the manual's lines are indexed by "Mesh". A
    model handed the raw spelling has to make the leap itself.
    """
    live, _fake = wired(monkeypatch)
    said = live.describe()
    assert 'keymap context "Mesh"' in said
    assert "edit mode" in said


def test_object_mode_maps_to_its_own_context(monkeypatch):
    state = {"mode": "OBJECT", "active": "Cube", "type": "MESH",
             "selected": ["Cube"], "modifiers": [], "materials": []}
    live, _fake = wired(monkeypatch, [{"status": "success", "result": {
        "executed": True, "result": json.dumps(state)}}])
    said = live.describe()
    assert 'keymap context "Object Mode"' in said


def test_what_is_selected_and_what_is_on_it_is_reported(monkeypatch):
    live, _fake = wired(monkeypatch)
    said = live.describe()
    assert "Cube" in said
    assert "Subdivision" in said
    assert "Material" in said


def test_NOTHING_ACTIVE_is_called_out_because_it_is_the_usual_answer(monkeypatch):
    """"It is not working" is usually nothing being selected. Saying so is
    most of what a live read is for.
    """
    state = {"mode": "OBJECT", "active": None, "type": None,
             "selected": [], "modifiers": [], "materials": []}
    live, _fake = wired(monkeypatch, [{"status": "success", "result": {
        "executed": True, "result": json.dumps(state)}}])
    said = live.describe()
    assert "NOTHING is active" in said
    assert "not working" in said


def test_nothing_selected_is_different_from_nothing_active(monkeypatch):
    state = {"mode": "OBJECT", "active": "Cube", "type": "MESH",
             "selected": [], "modifiers": [], "materials": []}
    live, _fake = wired(monkeypatch, [{"status": "success", "result": {
        "executed": True, "result": json.dumps(state)}}])
    said = live.describe()
    assert "nothing is SELECTED" in said


def test_it_tells_the_model_not_to_quote_another_context(monkeypatch):
    """The whole reason the mode is worth reading."""
    live, _fake = wired(monkeypatch)
    said = live.describe()
    assert "another context is wrong" in said
    assert "do not ask them what mode" in said


# --- when Blender is not there, which is the normal case ---------------------


def test_a_refused_connection_is_silence_not_an_error(monkeypatch):
    live, _fake = wired(monkeypatch, fail=ConnectionRefusedError("nope"))
    assert live.look() is None
    assert live.describe() == ""
    assert live.reachable is False


def test_a_refusal_is_remembered_so_every_turn_does_not_pay_for_it(monkeypatch):
    """A voice loop must not spend a connect attempt per turn on a port that
    is almost never open.
    """
    live, fake = wired(monkeypatch, fail=ConnectionRefusedError("nope"))
    assert live.look() is None
    before = len(fake.sent)
    for _ in range(5):
        assert live.look() is None
    assert len(fake.sent) == before, "it kept trying"


def test_the_connect_budget_is_short_and_the_reply_budget_is_not(monkeypatch):
    """Connecting to a dead port on loopback is instant; waiting for Blender
    to drain its queue is not.
    """
    live, fake = wired(monkeypatch)
    live.look()
    assert fake.timeouts[0] == scene.CONNECT_SECONDS
    assert fake.timeouts[1] == scene.REPLY_SECONDS
    assert scene.CONNECT_SECONDS < 1.0 < scene.REPLY_SECONDS


def test_an_error_reply_is_not_mistaken_for_state(monkeypatch):
    live, _fake = wired(monkeypatch, [{"status": "error",
                                       "message": "addon is confused"}])
    assert live.look() is None
    assert "confused" in (live.last_error or "")


@pytest.mark.parametrize("broken", [
    {"status": "success", "result": {"executed": True, "result": "not json"}},
    {"status": "success", "result": {"executed": True, "result": "[1,2,3]"}},
    {"status": "success", "result": {}},
    {"status": "success"},
    {},
])
def test_a_malformed_reply_is_survived(monkeypatch, broken):
    """Another application's add-on, across versions nobody here controls."""
    live, _fake = wired(monkeypatch, [broken])
    assert live.look() is None
    assert live.describe() == ""


def test_the_socket_is_always_closed(monkeypatch):
    live, fake = wired(monkeypatch)
    live.look()
    assert fake.closed
