"""Composio over REST, because the SDK would break the model client.

`composio` depends on `openai>=3` and `langchain-openai` requires `openai<3`.
Installing it upgrades the OpenAI client out from under the harness, the
planner, the answer path and the query rewriter - four things that work - to
get a convenience wrapper around HTTP calls. The REST API needs `httpx`, which
`meow/research.py` already pulls in.

This is a thin client on purpose. It knows how to authenticate, how to name a
tool, and how to report a failure; it holds no opinion about which tools exist,
because that belongs in `reader.py` and `sender.py` where the split is
enforced.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

BASE_URL = "https://backend.composio.dev/api/v3"

# Long enough for a mailbox query, short enough that a hung connector does not
# hold a voice turn open. A tool call that takes longer than this has failed in
# every way that matters to someone waiting for an answer.
TIMEOUT_SECONDS = 25


def _readable(status: int, body: str, tool: str) -> str:
    """Turn a connector's error into something a person can act on.

    The raw one is JSON - `{"error":{"message":"No connected account found for
    user ID default for toolkit gmail","code":1810,...}}` - and this gets read
    out loud. "Gmail is not connected" is the same fact in a form that says
    what to do about it.
    """
    text = str(body or "")
    toolkit = tool.split("_", 1)[0].lower() if "_" in tool else tool.lower()
    if "No connected account" in text:
        return (f"{toolkit} is not connected yet. Connect it at composio.dev, "
                f"under Apps, then try again")
    if status in (401, 403):
        return (f"composio refused the key. Check COMPOSIO_API_KEY, and that "
                f"{toolkit} is still connected")
    if status == 429:
        return "composio is rate limiting; wait a moment and try again"
    return f"{status} {text[:160]}"


@dataclass(frozen=True)
class Result:
    """What a connector call returned, or why it did not."""

    ok: bool
    data: dict
    error: str = ""

    def describe(self) -> str:
        if self.ok:
            return json.dumps(self.data)[:4000]
        return f"[connector failed: {self.error}]"


def this_install() -> str:
    """A stable id for this installation, made once and kept.

    The API key belongs to whoever BUILT Meow; the connected accounts belong
    to whoever is running it. Composio separates them by `user_id`, so this is
    the only thing keeping one person's mail out of another person's session -
    and the old default, the literal string "default", gave every install on
    every machine the same one. Two people, one developer key, one inbox
    between them.

    A random id rather than a username or an email: it needs to be unique and
    stable, and nothing else. It is not a secret and it identifies nobody.
    """
    folder = (Path(os.environ.get("USERPROFILE", Path.home()))
              / "Documents" / "Meow")
    folder.mkdir(parents=True, exist_ok=True)
    stored = folder / "install-id"
    try:
        existing = stored.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass  # First run, or unreadable - either way, make one.

    fresh = f"meow-{uuid.uuid4().hex[:16]}"
    try:
        stored.write_text(fresh, encoding="utf-8")
    except OSError:
        # Unwritable disk. A per-process id still beats a shared one: the
        # login is asked for again next run, which is annoying and correct.
        pass
    return fresh


class Composio:
    """Authenticated calls to Composio's tool-execution API."""

    def __init__(self, api_key: str | None = None,
                 user_id: str | None = None) -> None:
        from ..config import get

        self.api_key = api_key or get("COMPOSIO_API_KEY") or ""
        # Composio scopes connections to an "entity"; one person on one machine
        # is one entity, and naming it rather than defaulting keeps the
        # connection findable when there is more than one.
        self.user_id = user_id or get("COMPOSIO_USER_ID") or this_install()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key,
                "Content-Type": "application/json"}

    def execute(self, tool: str, arguments: dict) -> Result:
        """Run one Composio tool. Never raises.

        Not raising matters: a connector is the least reliable thing in the
        project - it is someone else's service, over the network, behind an
        OAuth token that can expire - and none of that is a reason for a voice
        turn to end in a traceback.
        """
        if not self.configured:
            return Result(False, {}, "COMPOSIO_API_KEY is not set")

        try:
            import httpx

            response = httpx.post(
                f"{BASE_URL}/tools/execute/{tool}",
                headers=self._headers(),
                json={"user_id": self.user_id, "arguments": arguments},
                timeout=TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                return Result(False, {},
                              _readable(response.status_code, response.text,
                                        tool))
            payload = response.json()
        except Exception as error:  # noqa: BLE001 - reported, never fatal
            return Result(False, {}, f"{type(error).__name__}: {error}")

        # Composio wraps a tool's own success flag inside its transport
        # success. A 200 that carries successful=false is a failure, and
        # reporting it as a success is how "sent" gets said about mail that
        # was rejected.
        if isinstance(payload, dict) and payload.get("successful") is False:
            return Result(False, payload.get("data") or {},
                          str(payload.get("error") or "the tool reported failure"))
        data = payload.get("data") if isinstance(payload, dict) else None
        return Result(True, data if isinstance(data, dict) else {"result": data})

    def connections(self) -> Result:
        """Which accounts are actually connected, for telling the user."""
        if not self.configured:
            return Result(False, {}, "COMPOSIO_API_KEY is not set")
        try:
            import httpx

            response = httpx.get(
                f"{BASE_URL}/connected_accounts",
                headers=self._headers(),
                params={"user_ids": self.user_id},
                timeout=TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                return Result(False, {},
                              f"{response.status_code} {response.text[:200]}")
            return Result(True, response.json())
        except Exception as error:  # noqa: BLE001
            return Result(False, {}, f"{type(error).__name__}: {error}")
