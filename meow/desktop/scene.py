"""What an application says about its own state, when the tree cannot see it.

Blender's whole accessibility tree is **seven nodes at depth three** - a title
bar and three window buttons. Measured with the whitelist off and the depth
cap removed, so it is a platform fact and not a narrow query. No screen
reader helps there, and the pixels are eight seconds and 52% accurate.

But Blender will simply tell you, if something is listening on its side. This
talks to the `blender-mcp` add-on's socket on `localhost:9876`, which the user
installed deliberately.

**WHY THIS MATTERS AND NOT MERELY HELPS.** `meow/knowledge/manuals.py`
measured the teaching model at 17/26 from memory and 26/26 with the
application's own keymap - and every one of those questions was HANDED the
context, because a shortcut is only correct inside one. X deletes the object
in Object Mode and opens a menu in Edit Mode. G is Move in the 3D View, the
UV Editor, the Graph Editor and the Node Editor. Nothing else in this project
can say which of those the user is looking at, so this is what makes the
manual's number real rather than an upper bound.

**No MCP client, no server process.** The add-on's socket speaks plain JSON -
`{"type": ..., "params": {...}}` in, `{"status": ..., "result": ...}` out -
and the MCP server that ships with it is a translation layer for MCP clients.
Talking to the socket directly gets every capability with none of the
machinery, which matters because it also means the add-on's telemetry
uploader is never in the path: that lives in their server, and this is not it.

⚠ **THE MODEL NEVER WRITES CODE THAT GOES DOWN THIS SOCKET.** The add-on's
`execute_code` is a bare `exec(code, {"bpy": bpy})` with no sandbox and no
authentication, so the danger in it is entirely about WHO composes the string.
Every question here is a module constant, written in this file, read-only by
inspection. There is no parameter, no formatting and no path from a spoken
sentence, a model reply or a fetched page to the code that runs. That is the
same containment rule the project already applies to web-derived names: the
untrusted side may say what to LOOK FOR and never what to DO.

**And nothing here changes the scene.** Every constant is a read followed by a
print. A tool that could move the user's geometry belongs behind the
confirmation gate in `meow/agent/risk.py`, and none is offered.

⚠ **The add-on refuses to serve in `blender -b`**, and says so: "commands
would never execute". Its command queue is drained by a `bpy.app.timers`
callback and there is no event loop in background mode. So this only ever
finds a Blender the user has open, which is also the only one worth asking
about.

⚠ **Auto-start ships as `default=True` and was patched to False on install.**
Upstream opens this socket on every Blender launch, whether or not anything
is there to use it - an unauthenticated code-execution port for the life of
the session. The user presses Connect when they want it.
"""

from __future__ import annotations

import json
import socket
import time

HOST = "127.0.0.1"
PORT = 9876

# Short, because this runs on the critical path of a spoken turn and the
# answer is "Blender is not listening" far more often than not. A refused
# connection on loopback comes back in microseconds; this budget is for the
# case where something is listening and busy.
CONNECT_SECONDS = 0.4

# Generous once connected: the add-on hands the command to Blender's main
# thread and waits for a timer to drain the queue, so a reply takes as long as
# Blender takes to get round to it. Measured at 45-64ms on an idle scene.
REPLY_SECONDS = 3.0

# How long a "nothing is listening" answer is believed before trying again.
# Without this, every turn of every session pays a connect attempt to a port
# that is almost never open. Short enough that pressing Connect in Blender is
# noticed within a sentence or two.
SILENCE_SECONDS = 20.0

# --- the questions, and the whole security argument -------------------------
#
# Constants. Not templates, not f-strings, nothing with a parameter. The
# add-on's execute_code runs whatever it is sent, so the only property worth
# having is that nothing outside this file can influence the string - and the
# way to have that property is to make it unreachable rather than to validate
# it.
#
# Each is a read and a print of JSON. No assignment to anything in the scene,
# no operator call, no import beyond bpy and json.

WHAT_IS_ON = (
    "import bpy, json\n"
    "c = bpy.context\n"
    "a = c.active_object\n"
    "print(json.dumps({\n"
    "  'mode': c.mode,\n"
    "  'active': getattr(a, 'name', None),\n"
    "  'type': getattr(a, 'type', None),\n"
    "  'selected': [o.name for o in c.selected_objects][:12],\n"
    "  'modifiers': [m.name for m in getattr(a, 'modifiers', [])][:12],\n"
    "  'materials': [s.name for s in getattr(a, 'material_slots', [])\n"
    "                if s.name][:8],\n"
    "  'frame': c.scene.frame_current,\n"
    "  'editor': getattr(getattr(c, 'area', None), 'type', None),\n"
    "}))"
)

# How Blender spells a mode against how a person says it. Blender reports
# EDIT_MESH, and a cat that says "you are in edit mesh" sounds like a
# database. The spelling is also what the keymap contexts are named after,
# which is why both are given to the model.
SPOKEN_MODES = {
    "OBJECT": ("Object Mode", "object mode"),
    "EDIT_MESH": ("Mesh", "edit mode"),
    "EDIT_CURVE": ("Curve", "edit mode on a curve"),
    "EDIT_SURFACE": ("Surface", "edit mode on a surface"),
    "EDIT_TEXT": ("Font", "edit mode on text"),
    "EDIT_ARMATURE": ("Armature", "edit mode on an armature"),
    "EDIT_METABALL": ("Metaball", "edit mode on a metaball"),
    "EDIT_LATTICE": ("Lattice", "edit mode on a lattice"),
    "POSE": ("Pose", "pose mode"),
    "SCULPT": ("Sculpt", "sculpt mode"),
    "PAINT_WEIGHT": ("Weight Paint", "weight paint mode"),
    "PAINT_VERTEX": ("Vertex Paint", "vertex paint mode"),
    "PAINT_TEXTURE": ("Image Paint", "texture paint mode"),
    "PARTICLE": ("Particle", "particle edit mode"),
}


class Scene:
    """A live read of Blender, or nothing at all.

    Holds no connection between calls: the add-on's server can be stopped and
    started from Blender's sidebar at any moment, and a socket kept open
    across that is a socket that looks fine and answers nothing.
    """

    def __init__(self, host: str = HOST, port: int = PORT) -> None:
        self._host, self._port = host, port
        self._quiet_until = 0.0
        self.last_error: str | None = None

    # --- talking to it ----------------------------------------------------

    def _ask(self, kind: str, **params):
        """One request. Never raises - a silent Blender is the normal case."""
        self.last_error = None
        if time.monotonic() < self._quiet_until:
            return None

        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(CONNECT_SECONDS)
        try:
            connection.connect((self._host, self._port))
            connection.settimeout(REPLY_SECONDS)
            connection.sendall(
                json.dumps({"type": kind, "params": params}).encode("utf-8"))
            received = b""
            while True:
                part = connection.recv(65536)
                if not part:
                    break
                received += part
                try:
                    # The add-on sends one JSON object and does not frame it,
                    # so a successful parse IS the end of the message.
                    return json.loads(received.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
            self.last_error = "blender closed the connection without replying"
            return None
        except (ConnectionRefusedError, OSError, socket.timeout) as error:
            # Nothing listening, or Blender is not running. Remembered for a
            # few seconds so this is not paid on every turn.
            self._quiet_until = time.monotonic() + SILENCE_SECONDS
            self.last_error = f"{type(error).__name__}"
            return None
        finally:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass

    @property
    def reachable(self) -> bool:
        """Is a Blender listening right now?

        Asked with a real question rather than a bare connect: the add-on
        accepts the socket on a worker thread and only answers once Blender's
        main thread drains the queue, so a connection that opens proves
        nothing about whether anybody is home.
        """
        return self.look() is not None

    def look(self):
        """Mode, selection, modifiers and materials, or None.

        `execute_code` is used because the add-on's twenty read-only commands
        do NOT report the mode - measured: `get_world_state_snapshot` returns
        twenty-one fields and not one of them is it, and the mode is the whole
        reason this file exists. The code is the constant above.
        """
        reply = self._ask("execute_code", code=WHAT_IS_ON)
        if not isinstance(reply, dict) or reply.get("status") != "success":
            if isinstance(reply, dict):
                self.last_error = str(reply.get("message") or "refused")
            return None
        try:
            printed = reply["result"]["result"]
            state = json.loads(printed.strip())
        except Exception as error:  # noqa: BLE001 - a bad read is not a crash
            self.last_error = f"{type(error).__name__}: {error}"
            return None
        return state if isinstance(state, dict) else None

    # --- saying it --------------------------------------------------------

    def describe(self, state=None) -> str:
        """The block for the prompt. Empty when Blender is not listening.

        Deliberately leads with the KEYMAP CONTEXT rather than with the mode's
        own spelling, because that is the word the manual's lines are indexed
        by: a model told "Mesh" can match the Ctrl+B line, and one told
        "EDIT_MESH" has to make the leap itself.
        """
        # Takes a state it has already been handed, so a turn that read the
        # scene for retrieval does not read it a second time to talk about.
        state = state if state is not None else self.look()
        if not state:
            return ""

        mode = str(state.get("mode") or "")
        context, spoken = SPOKEN_MODES.get(mode, (mode, mode.lower()))
        lines = [
            f"BLENDER IS OPEN AND TELLING YOU ITS OWN STATE, right now:",
            f"  they are in {spoken} - keymap context \"{context}\"",
        ]

        active = state.get("active")
        if active:
            kind = str(state.get("type") or "").lower()
            lines.append(f"  the active object is \"{active}\""
                         + (f", a {kind}" if kind else ""))
        else:
            lines.append("  NOTHING is active - most tools will do nothing, "
                         "and that is usually the answer to \"it is not "
                         "working\"")

        selected = state.get("selected") or []
        if selected:
            lines.append(f"  selected: {', '.join(str(s) for s in selected)}")
        elif active:
            lines.append("  nothing is SELECTED, which is not the same as "
                         "nothing being active - say so if a tool does "
                         "nothing")

        modifiers = state.get("modifiers") or []
        if modifiers:
            lines.append(f"  modifiers on it: "
                         f"{', '.join(str(m) for m in modifiers)}")
        materials = state.get("materials") or []
        if materials:
            lines.append(f"  materials on it: "
                         f"{', '.join(str(m) for m in materials)}")

        lines.append(
            "  This is read from Blender itself, so it is exact - do not ask "
            "them what mode they are in or what is selected, and do not guess "
            "from the screenshot. Only quote a shortcut from the context "
            "above; a key from another context is wrong here.")
        return "\n".join(lines)
