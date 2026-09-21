"""Microphone capture.

16 kHz mono PCM16, which is what AssemblyAI's streaming endpoint wants. The
device here runs at 44.1 kHz natively and PortAudio resamples on the way out -
asking for 16 kHz directly is cheaper and less error-prone than capturing at
44.1 and resampling in Python, and it halves the bytes on the wire.

Audio arrives on PortAudio's own high-priority callback thread. That thread must
never block or allocate much: if it stalls, the driver drops frames and the
user's sentence loses syllables. So the callback does one thing - copy bytes
into a queue - and everything else happens elsewhere.

The queue is bounded. An unbounded one turns a downstream stall into unbounded
memory growth and an ever-growing lag between speech and transcript, which is
worse than dropping a chunk and saying so.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass

import numpy
import sounddevice

SAMPLE_RATE = 16_000
CHANNELS = 1
DTYPE = "int16"
BYTES_PER_SAMPLE = 2

# 50ms per chunk. AssemblyAI accepts 50-1000ms; the low end of that range is
# what keeps interim transcripts arriving while the user is still talking,
# which is what the Jev router needs to run in parallel rather than after.
CHUNK_MILLISECONDS = 50
FRAMES_PER_CHUNK = SAMPLE_RATE * CHUNK_MILLISECONDS // 1000

# ~6 seconds of audio. Enough to ride out a network hiccup, small enough that a
# real stall is noticed rather than silently buffered.
MAX_QUEUED_CHUNKS = 120


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    sample_rate: int


def list_input_devices() -> list[AudioDevice]:
    devices = []
    for index, device in enumerate(sounddevice.query_devices()):
        if device["max_input_channels"] > 0:
            devices.append(AudioDevice(
                index=index,
                name=device["name"],
                sample_rate=int(device["default_samplerate"]),
            ))
    return devices


class Microphone:
    """Streams PCM16 chunks from the default input device."""

    def __init__(self, device: int | None = None) -> None:
        self.device = device
        self._chunks: queue.Queue[bytes] = queue.Queue(maxsize=MAX_QUEUED_CHUNKS)
        self._stream: sounddevice.RawInputStream | None = None
        self._running = threading.Event()

        # Read from the render loop to drive the cat's mouth, written from the
        # audio thread. A float assignment is atomic under the GIL, so this
        # needs no lock - and must not take one, because the audio thread
        # cannot afford to wait on anything.
        self.level = 0.0

        self.dropped_chunks = 0
        self.overflow_count = 0

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            # input_overflow means the driver produced audio faster than we
            # collected it. Counted rather than printed - printing from the
            # audio thread is itself a way to cause the next overflow.
            self.overflow_count += 1

        data = bytes(indata)

        samples = numpy.frombuffer(data, dtype=numpy.int16)
        if samples.size:
            # RMS, normalised against full scale. Used only for animation, so
            # precision matters far less than never blocking.
            self.level = float(
                numpy.sqrt(numpy.mean(numpy.square(samples.astype(numpy.float32))))
                / 32768.0
            )

        try:
            self._chunks.put_nowait(data)
        except queue.Full:
            self.dropped_chunks += 1

    def start(self) -> None:
        if self._stream is not None:
            return
        self._stream = sounddevice.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=FRAMES_PER_CHUNK,
            device=self.device,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=self._callback,
        )
        self._stream.start()
        self._running.set()

    def stop(self) -> None:
        self._running.clear()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        # Unblock anything waiting in chunks(). A sentinel is needed because a
        # consumer parked in queue.get() would otherwise wait out its timeout
        # after the stream is already gone.
        try:
            self._chunks.put_nowait(b"")
        except queue.Full:
            pass

    def chunks(self):
        """Yield PCM16 chunks until stopped.

        Blocking generator, meant for a worker thread. Never call this from the
        render loop - it would stall the cat whenever the user goes quiet.
        """
        while self._running.is_set():
            try:
                chunk = self._chunks.get(timeout=0.25)
            except queue.Empty:
                continue
            if not chunk:
                break
            yield chunk

    @property
    def seconds_buffered(self) -> float:
        return (self._chunks.qsize() * FRAMES_PER_CHUNK) / SAMPLE_RATE

    def __enter__(self) -> "Microphone":
        self.start()
        return self

    def __exit__(self, *_exc_info) -> None:
        self.stop()
