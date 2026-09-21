"""Listen to the voices this account can actually use, and pick one.

ElevenLabs splits voices into PREMADE - a small built-in set - and VOICE
LIBRARY, the community voices you add to your account. A free plan can use
library voices on the website but not through the API, which is why one can
sound perfect in the browser and return 402 from code.

This plays each premade voice saying the same line, so the choice is made by
ear rather than by reading names.

    python scripts/audition_voices.py
    python scripts/audition_voices.py --say "open word and write a letter"

Each line costs a handful of characters against the free quota. The whole run
is well under a hundred.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meow.config import elevenlabs_voice_id
from meow.console import use_utf8_console
from meow.voice import ElevenLabsSpeaker

# The premade set. These are the ones a free account can reach through the API.
PREMADE_VOICES = [
    ("Adam", "pNInz6obpgDQGcFmaJgB", "deep, calm, male"),
    ("Antoni", "ErXwobaYiN019PkySvjV", "warm, male"),
    ("Josh", "TxGEqnHWrfWFTfGW9XjX", "young, male"),
    ("Sam", "yoZ06aMxZJJ28mxfxg4B", "raspy, male"),
    ("Bella", "EXAVITQu4vr4xnSDxMaL", "soft, female"),
    ("Elli", "MF3mGyEYCl7XYWbV9V6O", "bright, female"),
    ("Domi", "AZnzlk1XvdvUeBnXmlld", "strong, female"),
]

DEFAULT_LINE = "hey, i can see your screen. that button saves your file."


def main() -> None:
    use_utf8_console()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--say", default=DEFAULT_LINE,
                        help="the line each voice reads")
    parser.add_argument("--gap", type=float, default=0.6,
                        help="seconds of silence between voices")
    args = parser.parse_args()

    configured = elevenlabs_voice_id()
    print(f'\n  each voice reads: "{args.say}"')
    if configured:
        known = {voice_id for _, voice_id, _ in PREMADE_VOICES}
        if configured not in known:
            print(f"\n  note: ELEVENLABS_VOICE_ID in .env is {configured},")
            print("        which is not a premade voice. On a free plan that")
            print("        returns 402 and falls back to Adam every time.")
    print()

    working: list[tuple[str, str]] = []
    for name, voice_id, description in PREMADE_VOICES:
        # A fresh speaker per voice so a refusal on one cannot silently leave
        # the next one auditioning a fallback instead of itself.
        speaker = ElevenLabsSpeaker(voice_id=voice_id)
        started = time.perf_counter()
        speaker.say(args.say)

        first = None
        while speaker.is_speaking:
            if first is None and speaker.level > 0.001:
                first = time.perf_counter() - started
            time.sleep(0.005)

        if speaker.last_error or speaker.voice_id != voice_id:
            print(f"  {name:<8} {description:<20} refused by this account")
            continue

        working.append((name, voice_id))
        print(f"  {name:<8} {description:<20} ok   "
              f"first audio {first * 1000:.0f} ms" if first
              else f"  {name:<8} {description:<20} ok   (silent)")
        time.sleep(args.gap)

    if not working:
        print("\n  no premade voice worked, which points at the key rather than")
        print("  the plan. Check ELEVENLABS_API_KEY has text to speech scope.")
        return

    print(f"\n  {len(working)} of {len(PREMADE_VOICES)} usable. To keep one, put")
    print("  its id in .env:\n")
    for name, voice_id in working:
        print(f"    ELEVENLABS_VOICE_ID={voice_id}    # {name}")


if __name__ == "__main__":
    main()
