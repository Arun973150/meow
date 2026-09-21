"""Keys and settings.

Reads `.env` from the project root. No dependency on python-dotenv: the parser
below is twenty lines, and one fewer install is one fewer thing to go wrong on a
machine that has not run `pip install` yet.

**Nothing here ever logs or prints a key value.** `describe()` reports only
whether a key is present and shows the last four characters, which is enough to
tell two keys apart and useless to anyone reading over a shoulder or scrolling a
terminal recording. A missing key raises with instructions rather than failing
somewhere deep inside an HTTP client with a 401.

A `.env` file is the right answer for one developer on one machine. It is not
how keys should reach a shipped client - see the note at the bottom of
`.env.example`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


class MissingKey(RuntimeError):
    """A required key is not set.

    Carries the name and where to get it. Raised at startup rather than left to
    surface as a 401 from inside a provider SDK, which is a far worse place to
    learn that a file was never filled in.
    """


# name -> (what it is for, where to get it)
KEY_SOURCES = {
    "OPENAI_API_KEY": ("the harness model", "platform.openai.com/api-keys"),
    "ASSEMBLYAI_API_KEY": ("streaming speech to text", "assemblyai.com/dashboard"),
    "ELEVENLABS_API_KEY": ("text to speech", "elevenlabs.io -> Profile -> API Keys"),
    "TYPESAFE_API_KEY": ("Jev routing", "typesafe.ai"),
    "LANGSMITH_API_KEY": ("tracing", "smith.langchain.com -> Settings -> API Keys"),
}


def load_env(path: Path | None = None, override: bool = False) -> dict[str, str]:
    """Read a .env file into os.environ and return what it contained.

    Real environment variables win by default. A key exported in the shell is a
    deliberate act, and a stale line in a file should not quietly beat it.
    """
    env_path = path or ENV_PATH
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()

        # Strip one layer of matching quotes. Keys do not contain them, but
        # people paste them in anyway and the result is a 401 with no clue why.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        if not value:
            continue

        loaded[name] = value
        if override or name not in os.environ:
            os.environ[name] = value
    return loaded


def get(name: str, default: str | None = None) -> str | None:
    load_env()
    value = os.environ.get(name, default)
    return value or None


def require(name: str) -> str:
    """Fetch a key, or raise with instructions for setting it."""
    value = get(name)
    if value:
        return value

    purpose, source = KEY_SOURCES.get(name, ("", ""))
    detail = f" ({purpose}, from {source})" if purpose else ""
    raise MissingKey(
        f"{name} is not set{detail}.\n\n"
        f"  1. copy .env.example to .env\n"
        f"  2. paste the key after {name}=\n"
        f"  3. python scripts/check_keys.py\n\n"
        f"  looked in: {ENV_PATH}"
    )


def mask(value: str) -> str:
    """Last four characters only - enough to tell two keys apart."""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * 8}{value[-4:]}"


@dataclass(frozen=True)
class KeyStatus:
    name: str
    present: bool
    masked: str
    purpose: str
    source: str


def describe() -> list[KeyStatus]:
    """Which keys are set, without revealing any of them."""
    load_env()
    statuses: list[KeyStatus] = []
    for name, (purpose, source) in KEY_SOURCES.items():
        value = os.environ.get(name, "")
        statuses.append(KeyStatus(
            name=name,
            present=bool(value),
            masked=mask(value) if value else "not set",
            purpose=purpose,
            source=source,
        ))
    return statuses


# --- convenience accessors, so callers do not pass string literals around ----

def openai_api_key() -> str:
    return require("OPENAI_API_KEY")


def assemblyai_api_key() -> str:
    return require("ASSEMBLYAI_API_KEY")


def elevenlabs_api_key() -> str:
    return require("ELEVENLABS_API_KEY")


def elevenlabs_voice_id() -> str | None:
    return get("ELEVENLABS_VOICE_ID")
