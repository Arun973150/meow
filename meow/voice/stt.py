"""Speech to text.

A `Transcriber` protocol with one implementation today, AssemblyAI v3 streaming.
The protocol exists because 00-scope.md leaves cloud-versus-local voice open:
swapping in a local model later should mean writing one class, not rewriting the
loop that uses it.

Two decisions carry all the latency here.

**Streaming, not batch.** Interim transcripts arrive while the user is still
talking, which is what lets the Jev router classify intent in parallel with
speech rather than after it. A batch recogniser cannot do that at any price -
it has nothing to say until the utterance is over.

**Fire on `end_of_turn`, not `turn_is_formatted`.** AssemblyAI sends the turn
twice: once when it decides the speaker has stopped, and again once punctuation
and capitalisation are applied. The model does not need the second one. Waiting
for it adds latency for a difference the model cannot use. Clicky accepts either
and so responds later than it needs to.

Everything network-facing runs on a worker thread and reports through a queue.
The render loop polls; it never waits. A cat that freezes mid-blink because a
websocket is slow has given the game away.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Iterable, Protocol

from assemblyai.streaming import v3

from ..config import assemblyai_api_key
from .microphone import SAMPLE_RATE

# How long the user may pause before Meow decides they have finished. A second
# and a quarter is long enough to think of the next word and short enough that
# a finished sentence does not sit there.
# How loud audio must be to count as speech. The library's default assumes a
# headset in a quiet room; a laptop microphone in a shared one picks up
# conversation two metres away and transcribes it as the user. Tunable by ear -
# raise it if the cat hears the room, lower it if it misses a quiet sentence.
VAD_THRESHOLD = 0.55

PATIENCE_SECONDS = 1.25


@dataclass(frozen=True)
class Transcript:
    """One update from the recogniser.

    `is_final` means the speaker has stopped, not that the text is polished.
    `is_formatted` is the later, punctuated version of the same turn - useful
    for showing the user, not worth waiting for before acting.
    """

    text: str
    is_final: bool
    is_formatted: bool = False
    confidence: float | None = None

    @property
    def is_actionable(self) -> bool:
        return self.is_final and bool(self.text.strip())


class Transcriber(Protocol):
    """What the voice loop needs from any speech recogniser."""

    def start(self, audio: Iterable[bytes]) -> None: ...
    def poll(self) -> list[Transcript]: ...
    def stop(self) -> None: ...
    @property
    def connected(self) -> bool: ...


class AssemblyAIStreaming:
    """AssemblyAI v3 streaming over a websocket, on a worker thread."""

    def __init__(self, api_key: str | None = None,
                 format_turns: bool = True,
                 patience_seconds: float = PATIENCE_SECONDS,
                 language: str = "en",
                 microphone_distance: str = "near-field",
                 vad_threshold: float = VAD_THRESHOLD) -> None:
        # Resolved now rather than at connect time, so a missing key fails
        # immediately with instructions instead of inside a handshake.
        self._api_key = api_key or assemblyai_api_key()
        self._format_turns = format_turns
        self._patience = patience_seconds
        self._language = language
        self._vad_threshold = vad_threshold
        # Resolved once. The enum rejects a bad string here, at construction,
        # rather than inside the websocket handshake where the error arrives
        # as a connection failure with no hint of the cause.
        self._noise_model = v3.NoiseSuppressionModel(microphone_distance)

        self._client: v3.StreamingClient | None = None
        self._worker: threading.Thread | None = None
        self._transcripts: queue.Queue[Transcript] = queue.Queue()
        self._connected = threading.Event()
        self._stopping = threading.Event()
        self.last_error: str | None = None

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def start(self, audio: Iterable[bytes]) -> None:
        if self._worker is not None:
            return

        self._stopping.clear()
        self._client = v3.StreamingClient(
            v3.StreamingClientOptions(api_key=self._api_key)
        )
        self._client.on(v3.StreamingEvents.Begin, self._on_begin)
        self._client.on(v3.StreamingEvents.Turn, self._on_turn)
        self._client.on(v3.StreamingEvents.Termination, self._on_termination)
        self._client.on(v3.StreamingEvents.Error, self._on_error)

        def run() -> None:
            try:
                self._client.connect(v3.StreamingParameters(
                    sample_rate=SAMPLE_RATE,
                    # The English model, explicitly. The default is the
                    # MULTILINGUAL one, which hears accented English and
                    # renders it in the script of whichever language it
                    # settles on - "open notepad then type hi my name is
                    # srijaa" arrived as Devanagari transliteration. The words
                    # were right and the script was not, so the harness got a
                    # sentence it could not act on and would have typed
                    # Devanagari into Notepad. Nothing about it reads as a
                    # transcription failure in the log, which is what makes it
                    # expensive to find.
                    speech_model=v3.SpeechModel.universal_streaming_english,
                    language_code=self._language,
                    # And do not reconsider per turn. Detection drifting on
                    # one noisy sentence is exactly the failure above.
                    language_detection=False,
                    # Near-field: a laptop microphone a forearm from the
                    # speaker. It suppresses the room rather than the person,
                    # which is the complaint - a conversation across the room
                    # was being transcribed as if it were the user.
                    noise_suppression_model=self._noise_model,
                    voice_focus=self._noise_model,
                    # How loud something must be before it counts as speech at
                    # all. Raised from the default, which is tuned for a
                    # headset in a quiet room.
                    vad_threshold=self._vad_threshold,
                    # Ask for the formatted turn as well. It arrives after the
                    # unformatted one and is nicer to display; we simply do not
                    # WAIT for it before acting.
                    format_turns=self._format_turns,
                    # How long a pause is allowed before the sentence is
                    # considered finished. The defaults are tuned for dictation,
                    # where people speak in a steady stream; someone asking a
                    # computer for something pauses to think mid-sentence and
                    # gets cut off. Both bounds are raised: raising one alone
                    # still ends the turn on the other.
                    min_turn_silence=int(self._patience * 1000),
                    max_turn_silence=int(self._patience * 1000 * 1.6),
                ))
                # Blocks, pulling from the microphone generator until it ends.
                self._client.stream(audio)
            except Exception as error:  # noqa: BLE001 - surfaced, not swallowed
                if not self._stopping.is_set():
                    self.last_error = f"{type(error).__name__}: {error}"
            finally:
                self._connected.clear()

        self._worker = threading.Thread(
            target=run, name="assemblyai-stream", daemon=True
        )
        self._worker.start()

    def poll(self) -> list[Transcript]:
        """Everything received since the last call. Never blocks."""
        received: list[Transcript] = []
        while True:
            try:
                received.append(self._transcripts.get_nowait())
            except queue.Empty:
                return received

    def stop(self) -> None:
        self._stopping.set()
        if self._client is not None:
            try:
                self._client.disconnect(terminate=True)
            except Exception:  # noqa: BLE001 - teardown must not raise
                pass
            self._client = None
        if self._worker is not None:
            self._worker.join(timeout=3.0)
            self._worker = None
        self._connected.clear()

    # --- websocket callbacks, all on the worker thread -------------------

    def _on_begin(self, _client, event: v3.BeginEvent) -> None:
        self._connected.set()

    def _on_turn(self, _client, event: v3.TurnEvent) -> None:
        text = (event.transcript or "").strip()
        if not text:
            return
        self._transcripts.put(Transcript(
            text=text,
            is_final=bool(event.end_of_turn),
            is_formatted=bool(event.turn_is_formatted),
            confidence=getattr(event, "end_of_turn_confidence", None),
        ))

    def _on_termination(self, _client, _event) -> None:
        self._connected.clear()

    def _on_error(self, _client, error) -> None:
        self.last_error = str(error)
        self._connected.clear()


class NullTranscriber:
    """Does nothing. Lets the loop be exercised with no network or key."""

    def __init__(self) -> None:
        self._connected = False

    def start(self, audio: Iterable[bytes]) -> None:
        self._connected = True

    def poll(self) -> list[Transcript]:
        return []

    def stop(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected
