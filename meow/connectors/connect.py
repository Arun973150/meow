"""Asking for access when it is needed, rather than before anything works.

The first version required going to a dashboard and connecting Gmail by hand
before the cat could read a single message. That is the wrong shape: a
companion should ask for what it needs at the moment it needs it, the way an
application asks to use your microphone when you press record, not in a setup
step you complete without knowing which parts you will ever use.

So: *"what's in my inbox"* on a machine with no Gmail connected opens the
Google login, says so out loud, and carries on the moment it is done.

**Composio-managed OAuth**, so there is no Google app to register and no client
secret on this machine. Composio holds the token; this holds only a key that
names which connection to use.

The flow, in the order the API wants it:

    auth config   POST /auth_configs        once per toolkit, reusable
    link          POST /connected_accounts/link   -> a redirect_url
    the browser   the user logs in and grants
    poll          GET  /connected_accounts/{id}   until ACTIVE

**Consent belongs to the user, and so does the browser.** This opens a page and
waits; it cannot click through a consent screen, cannot choose an account, and
times out rather than nagging. If they close the tab, nothing is connected and
the cat says so.
"""

from __future__ import annotations

import time
import webbrowser
from dataclasses import dataclass

from .composio import BASE_URL, TIMEOUT_SECONDS, Composio

# How long to wait for someone to finish logging in. Long enough to find the
# right Google account and read a consent screen, short enough that a forgotten
# tab does not hold a background task open all afternoon.
LOGIN_SECONDS = 180.0
POLL_SECONDS = 2.0

# Which toolkit each tool belongs to. Composio names tools by toolkit prefix,
# so this is derivable - but the derived name is wrong for exactly the ones
# that matter (GOOGLECALENDAR_* is "googlecalendar"), and guessing wrong means
# opening the login for the wrong service.
TOOLKITS = {
    "GMAIL": "gmail",
    "GOOGLECALENDAR": "googlecalendar",
    "YOUTUBE": "youtube",
    "SLACK": "slack",
}

FRIENDLY = {
    "gmail": "gmail",
    "googlecalendar": "your google calendar",
    "youtube": "youtube",
    "slack": "slack",
}


def toolkit_for(tool: str) -> str:
    """Which service a tool belongs to."""
    head = str(tool).split("_", 1)[0].upper()
    return TOOLKITS.get(head, head.lower())


@dataclass
class Connection:
    """What happened when access was asked for."""

    ok: bool
    toolkit: str
    detail: str = ""
    url: str = ""

    def spoken(self) -> str:
        friendly = FRIENDLY.get(self.toolkit, self.toolkit)
        if self.ok:
            return f"{friendly} is connected now"
        return f"i could not get access to {friendly}. {self.detail}"


class Connector:
    """Gets access to a service, by asking the user to log in."""

    def __init__(self, composio: Composio | None = None) -> None:
        self.composio = composio or Composio()
        # Auth configs are per-toolkit and reusable, so making one twice is
        # waste rather than harm. Remembered for the life of the process.
        self._configs: dict[str, str] = {}

    # --- the pieces -------------------------------------------------------

    def _post(self, path: str, body: dict):
        import httpx

        return httpx.post(f"{BASE_URL}{path}",
                          headers={"x-api-key": self.composio.api_key,
                                   "Content-Type": "application/json"},
                          json=body, timeout=TIMEOUT_SECONDS)

    def _get(self, path: str, params: dict | None = None):
        import httpx

        return httpx.get(f"{BASE_URL}{path}",
                         headers={"x-api-key": self.composio.api_key},
                         params=params or {}, timeout=TIMEOUT_SECONDS)

    def is_connected(self, toolkit: str) -> bool:
        """Is this service already usable? Cheap, and never raises."""
        try:
            response = self._get("/connected_accounts",
                                 {"user_ids": self.composio.user_id,
                                  "toolkit_slugs": toolkit})
            if response.status_code >= 400:
                return False
            items = response.json().get("items") or []
            return any(str(item.get("status", "")).upper() == "ACTIVE"
                       for item in items if isinstance(item, dict))
        except Exception:  # noqa: BLE001
            return False

    def _auth_config(self, toolkit: str) -> str | None:
        """An auth config for this toolkit, made once and reused."""
        if toolkit in self._configs:
            return self._configs[toolkit]
        try:
            # An existing one first. Making a second is not harmful and is
            # clutter in somebody's account, which is a small rudeness worth
            # avoiding.
            existing = self._get("/auth_configs", {"toolkit_slug": toolkit})
            if existing.status_code < 400:
                for item in existing.json().get("items") or []:
                    if isinstance(item, dict) and item.get("id"):
                        self._configs[toolkit] = item["id"]
                        return item["id"]

            made = self._post("/auth_configs", {
                "toolkit": {"slug": toolkit},
                "auth_config": {"type": "use_composio_managed_auth"},
            })
            if made.status_code >= 400:
                return None
            identifier = (made.json().get("auth_config") or {}).get("id")
            if identifier:
                self._configs[toolkit] = identifier
            return identifier
        except Exception:  # noqa: BLE001
            return None

    # --- the whole thing --------------------------------------------------

    def ensure(self, toolkit: str, announce=None,
               open_browser: bool = True,
               seconds: float = LOGIN_SECONDS) -> Connection:
        """Make sure this service is usable, asking the user to log in if not.

        `announce` is called with a sentence to say out loud before the browser
        opens, because a login tab appearing unbidden is alarming and the same
        tab after "i need access to your gmail, opening the login now" is
        obvious.

        Blocks while the user logs in, which is correct: whatever asked for
        this cannot continue without it, and a background task waiting on a
        person is exactly what `TaskState.WAITING` is for.
        """
        if not self.composio.configured:
            return Connection(False, toolkit,
                              "there is no composio key set")

        if self.is_connected(toolkit):
            return Connection(True, toolkit, "already connected")

        config = self._auth_config(toolkit)
        if config is None:
            return Connection(False, toolkit,
                              "could not set up the connection")

        try:
            link = self._post("/connected_accounts/link",
                              {"auth_config_id": config,
                               "user_id": self.composio.user_id})
            if link.status_code >= 400:
                return Connection(False, toolkit,
                                  f"composio said {link.status_code}")
            payload = link.json()
        except Exception as error:  # noqa: BLE001
            return Connection(False, toolkit, f"{type(error).__name__}")

        url = payload.get("redirect_url") or ""
        account = payload.get("connected_account_id") or ""
        if not url:
            return Connection(False, toolkit, "no login link came back")

        friendly = FRIENDLY.get(toolkit, toolkit)
        if announce is not None:
            announce(f"i need access to {friendly}. opening the login now, "
                     f"sign in and i will carry on")

        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001 - the url is still returned
                pass

        if self._wait_for(account, toolkit, seconds):
            return Connection(True, toolkit, "connected", url)
        return Connection(False, toolkit,
                          "the login was not finished, so nothing changed",
                          url)

    def _wait_for(self, account_id: str, toolkit: str,
                  seconds: float) -> bool:
        """Poll until the account is live, or give up.

        Checks the account by id AND the toolkit generally, because someone
        who already had a tab open may complete a different connection than
        the one just created, and the question that matters is whether the
        service is usable rather than whether one particular row went green.
        """
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            try:
                if account_id:
                    response = self._get(f"/connected_accounts/{account_id}")
                    if response.status_code < 400:
                        status = str(response.json().get("status", "")).upper()
                        if status == "ACTIVE":
                            return True
                        if status in ("FAILED", "EXPIRED"):
                            return False
                if self.is_connected(toolkit):
                    return True
            except Exception:  # noqa: BLE001 - keep waiting
                pass
            time.sleep(POLL_SECONDS)
        return False
