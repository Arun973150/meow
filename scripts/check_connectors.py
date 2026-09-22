"""What the connectors can reach, and connecting anything that is missing.

    python scripts/check_connectors.py                 # just look
    python scripts/check_connectors.py --connect youtube
    python scripts/check_connectors.py --read youtube  # actually fetch something

Separate from `check_keys.py` because a connector has two states a key does
not: the key can be right while no account is attached, and an account can be
attached while its token has expired. "It is configured" answers neither
question, so this asks the service.

`--connect` opens a browser and waits. Nothing here can click through a consent
screen or choose an account; it opens the page the same way the cat does and
reports what happened.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meow.config import get
from meow.connectors import Connector, Reader
from meow.connectors.composio import Composio, this_install
from meow.console import quiet_library_warnings, use_utf8_console

# The ones Meow has tools for. YouTube first because it is the safe one to try:
# public captions, no private data, and nothing it could send.
SERVICES = ("youtube", "gmail", "googlecalendar")

FRIENDLY = {"youtube": "YouTube",
            "gmail": "Gmail",
            "googlecalendar": "Google Calendar"}


def main() -> None:
    use_utf8_console()
    quiet_library_warnings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", default="",
                        help="open the login for one service")
    parser.add_argument("--read", default="",
                        help="fetch something, to prove it really works")
    arguments = parser.parse_args()

    key = get("COMPOSIO_API_KEY")
    user = get("COMPOSIO_USER_ID") or this_install()
    print(f"\n  key    {'set (' + str(len(key)) + ' chars)' if key else 'NOT SET'}")
    print(f"  user   {user}")

    if not key:
        print("\n  Get one at composio.dev -> Settings -> API Keys, then put it")
        print("  in .env as COMPOSIO_API_KEY. Nothing below can work without it.\n")
        raise SystemExit(1)

    composio = Composio(user_id=user)
    connector = Connector(composio)

    reachable = composio.connections()
    if not reachable.ok:
        print(f"\n  composio would not answer: {reachable.error}\n")
        raise SystemExit(1)

    print("\n  " + "-" * 52)
    for service in SERVICES:
        connected = connector.is_connected(service)
        mark = "connected" if connected else "not connected"
        print(f"  {FRIENDLY[service]:18} {mark}")
    print("  " + "-" * 52)

    if arguments.connect:
        service = arguments.connect.strip().lower()
        print(f"\n  opening the login for {FRIENDLY.get(service, service)}...")
        print("  a browser tab will appear - sign in and come back here.\n")
        result = connector.ensure(service,
                                  announce=lambda text: print(f"  {text}"))
        print(f"\n  {'connected' if result.ok else 'not connected'}: "
              f"{result.detail}")
        if not result.ok and result.url:
            print(f"  the link, if the browser did not open: {result.url}")
        return

    if arguments.read:
        service = arguments.read.strip().lower()
        reader = Reader(composio, announce=lambda text: print(f"  {text}"))
        print(f"\n  reading something from {FRIENDLY.get(service, service)}...\n")
        if service == "youtube":
            # A video with captions, so a failure means the connector rather
            # than the video.
            answer = reader.video_transcript(
                "https://www.youtube.com/watch?v=aircAruvnKk")
        elif service == "gmail":
            answer = reader.inbox("newer_than:7d")
        else:
            answer = reader.agenda()
        print("  " + answer[:700].replace("\n", "\n  "))
        return

    print("\n  To connect one:   python scripts/check_connectors.py "
          "--connect youtube")
    print("  To prove it works: python scripts/check_connectors.py "
          "--read youtube")
    print("\n  Or just run the cat and ask - it opens the login itself:")
    print('      "what is this video about" with a youtube link')
    print('      "what is in my inbox"\n')


if __name__ == "__main__":
    main()
