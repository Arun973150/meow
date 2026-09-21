"""Text to speech.

A `Speaker` protocol with an ElevenLabs implementation, matching the
`Transcriber` split on the input side and for the same reason - 00-scope.md
leaves cloud-versus-local voice open, and Kokoro-82M is a live option for a
CPU-only machine.

Three decisions, all about latency or about not lying to the user.

**PCM, not MP3.** ElevenLabs will return either. MP3 needs a decoder - ffmpeg
is not installed on this machine until Phase 3 - and decoding adds a step
between bytes arriving and sound coming out. `pcm_16000` feeds straight into an
output stream. It costs more bandwidth and saves the thing that matters.

**`eleven_flash_v2_5`.** Their low-latency model, around 75ms to first byte
against several hundred for the quality models. On a voice assistant the gap
between 75ms and 400ms is the difference between a companion and a kiosk.

**Playback is interruptible.** `stop()` cuts the audio immediately rather than
draining the buffer. A user who starts talking over the cat means it, and an
assistant that keeps talking for two more seconds is the single most irritating
thing this kind of software does.

Playback exposes `level`, the amplitude of what is coming out of the speaker
right now, which drives the cat's mouth. The mouth then moves because sound is
being made, rather than on a timer that happens to look similar.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

import numpy
import sounddevice

from ..config import elevenlabs_api_key, elevenlabs_voice_id

# Matches the microphone, so both directions speak the same format and nothing
# in the pipeline needs a resampler.
SAMPLE_RATE = 16_000
MODEL_ID = "eleven_flash_v2_5"
OUTPUT_FORMAT = "pcm_16000"

# A PREMADE voice. That distinction is load-bearing: free accounts get 402
# "Free users cannot use library voices via the API" for anything added from the
# Voice Library, and the error arrives per-request rather than at setup, so it
# looks like the code is broken rather than the account.
#
# Verified working on a free account: Adam. Rachel (21m00Tcm4TlvDq8ikWAM), long
# the canonical example in ElevenLabs docs, now returns 402 on free.
DEFAULT_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Adam

# Tried in order if the configured voice is refused.
PREMADE_FALLBACKS = (
    "pNInz6obpgDQGcFmaJgB",  # Adam
    "EXAVITQu4vr4xnSDxMaL",  # Bella
    "TxGEqnHWrfWFTfGW9XjX",  # Josh
)

PLAYBACK_BLOCK_FRAMES = 1024


class Speaker(Protocol):
    """What the voice loop needs from any text to speech provider."""

    def say(self, text: str) -> None: ...
    def stop(self) -> None: ...
    @property
    def is_speaking(self) -> bool: ...
    @property
    def level(self) -> float: ...


class ElevenLabsSpeaker:
    """Streams speech from ElevenLabs and plays it as it arrives."""

    def __init__(self, voice_id: str | None = None,
                 api_key: str | None = None) -> None:
        from elevenlabs.client import ElevenLabs

        # Resolved now so a missing key fails with instructions rather than
        # inside an HTTP call halfway through a sentence.
        self._client = ElevenLabs(api_key=api_key or elevenlabs_api_key())
        self.voice_id = voice_id or elevenlabs_voice_id() or DEFAULT_VOICE_ID

        self._worker: threading.Thread | None = None
        self._playing = threading.Event()
        # Held so stop() can abort the device from the caller's thread. The
        # worker is usually blocked inside stream.write() waiting for buffer
        # space, so waiting for it to notice a flag is not an option.
        self._stream: sounddevice.RawOutputStream | None = None
        # Bumped on every stop(), so audio from a cancelled utterance that is
        # still in flight can tell that it is stale and drop itself.
        self._generation = 0
        self._lock = threading.Lock()

        self.level = 0.0
        self.last_error: str | None = None
        # Announced once, not silently and not every sentence.
        self._warned_about_voice = False

    @property
    def is_speaking(self) -> bool:
        return self._playing.is_set()

    def say(self, text: str) -> None:
        """Speak a line, replacing anything currently being said."""
        if not text.strip():
            return
        self.stop()

        with self._lock:
            self._generation += 1
            generation = self._generation

        self._playing.set()
        self._worker = threading.Thread(
            target=self._run, args=(text, generation),
            name="tts-playback", daemon=True,
        )
        self._worker.start()

    def stop(self) -> None:
        """Cut playback now, and make sure the device is really closed.

        `abort()` rather than `stop()` on the device: abort discards audio
        already handed to the driver, where stop lets it drain. Draining means
        "stop" is heard as "stop in about a second", which the user experiences
        as being ignored.

        Then it JOINS the worker. That is not tidiness - a daemon thread still
        inside PortAudio when the interpreter tears down segfaults the process,
        which is exactly what happened the first time this ran.
        """
        with self._lock:
            self._generation += 1
            stream = self._stream
        self._playing.clear()
        self.level = 0.0

        if stream is not None:
            try:
                stream.abort()
            except Exception:  # noqa: BLE001 - already going away
                pass

        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2.0)
            self._worker = None

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation

    def _run(self, text: str, generation: int) -> None:
        stream = None
        try:
            audio = self._client.text_to_speech.stream(
                voice_id=self.voice_id,
                text=text,
                model_id=MODEL_ID,
                output_format=OUTPUT_FORMAT,
                # 0 keeps the default quality. Higher values trade
                # pronunciation accuracy for a little latency, which is a bad
                # bargain when the cat is reading out filenames.
                optimize_streaming_latency=0,
                request_options={
                    # No retries. The failure this actually hits is 402 on a
                    # Voice Library voice, which will never succeed no matter
                    # how many times it is asked - and the SDK default turned a
                    # refusal that should be instant into a 14 SECOND wait
                    # before the fallback voice could even start.
                    "max_retries": 0,
                    # The SDK default is 240s. Four minutes of silence is not a
                    # failure mode a voice assistant can have; better to give up
                    # and say nothing than to answer a question from last week.
                    "timeout_in_seconds": 15,
                },
            )

            stream = sounddevice.RawOutputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                blocksize=PLAYBACK_BLOCK_FRAMES,
            )
            with self._lock:
                self._stream = stream
            stream.start()

            for chunk in audio:
                if not self._is_current(generation):
                    break  # superseded or stopped; drop the rest
                if not chunk:
                    continue

                samples = numpy.frombuffer(chunk, dtype=numpy.int16)
                if samples.size:
                    self.level = float(
                        numpy.sqrt(numpy.mean(
                            numpy.square(samples.astype(numpy.float32))
                        )) / 32768.0
                    )
                stream.write(chunk)

        except Exception as error:  # noqa: BLE001 - surfaced, not swallowed
            if not self._is_current(generation):
                return
            if self._is_voice_refused(error) and self._fall_back_to_premade():
                # Retry once on a voice this account can actually use. A silent
                # substitution would be worse than the error, so it is printed -
                # but a cat that says nothing at all is worse than either.
                self._run(text, generation)
                return
            self.last_error = f"{type(error).__name__}: {error}"
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001 - teardown must not raise
                    pass
                with self._lock:
                    if self._stream is stream:
                        self._stream = None
            if self._is_current(generation):
                self._playing.clear()
                self.level = 0.0


    @staticmethod
    def _is_voice_refused(error: Exception) -> bool:
        """Is this the free-tier library-voice refusal, rather than a real fault?"""
        if getattr(error, "status_code", None) != 402:
            return False
        return "voice" in str(getattr(error, "body", "") or error).lower()

    def _fall_back_to_premade(self) -> bool:
        for candidate in PREMADE_FALLBACKS:
            if candidate != self.voice_id:
                if not self._warned_about_voice:
                    self._warned_about_voice = True
                    print(
                        f"\n  ElevenLabs refused voice {self.voice_id} — free "
                        f"accounts cannot use Voice Library voices via the "
                        f"API.\n  Falling back to a premade voice "
                        f"({candidate}).\n  To fix this permanently, set "
                        f"ELEVENLABS_VOICE_ID in .env to a premade voice, or "
                        f"clear it.\n"
                    )
                self.voice_id = candidate
                return True
        return False


class SilentSpeaker:
    """Says nothing. Lets the loop run with no key, no network and no noise."""

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.last_error: str | None = None

    def say(self, text: str) -> None:
        if text.strip():
            self.spoken.append(text)

    def stop(self) -> None:
        pass

    @property
    def is_speaking(self) -> bool:
        return False

    @property
    def level(self) -> float:
        return 0.0


class SpeechQueue:
    """Plays utterances one after another.

    `Speaker.say()` replaces whatever is currently being said, which is correct
    for a new answer and wrong for the second sentence of the same answer.
    Sentence-chunked speech needs both: sentences queue behind each other, and a
    new question clears the lot.

    Barge-in is `clear()`. It drops everything pending as well as cutting what
    is playing - stopping the current sentence only to start the next one is not
    an interruption, it is a pause.
    """

    def __init__(self, speaker) -> None:
        self._speaker = speaker
        self._pending: list[str] = []
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._generation = 0

    @property
    def level(self) -> float:
        return self._speaker.level

    @property
    def is_busy(self) -> bool:
        with self._lock:
            if self._pending:
                return True
        return self._speaker.is_speaking

    def enqueue(self, text: str) -> None:
        if not text.strip():
            return
        with self._lock:
            self._pending.append(text)
            if self._worker is not None and self._worker.is_alive():
                return
            self._generation += 1
            generation = self._generation
            self._worker = threading.Thread(
                target=self._drain, args=(generation,),
                name="speech-queue", daemon=True,
            )
            worker = self._worker
        worker.start()

    def clear(self) -> None:
        with self._lock:
            self._generation += 1
            self._pending.clear()
        self._speaker.stop()

    def _drain(self, generation: int) -> None:
        while True:
            with self._lock:
                if generation != self._generation or not self._pending:
                    return
                text = self._pending.pop(0)

            self._speaker.say(text)
            # Wait for this utterance to finish before starting the next.
            # Polling rather than a callback because Speaker is a protocol and
            # a provider is not required to offer one.
            while self._speaker.is_speaking:
                if generation != self._generation:
                    return
                time.sleep(0.01)
