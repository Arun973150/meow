"""Ears and mouth.

Both sides sit behind a protocol, because 00-scope.md leaves cloud-versus-local
voice open. Swapping a provider should mean writing one class, not rewriting the
loop around it.
"""

from .microphone import Microphone, list_input_devices
from .stt import AssemblyAIStreaming, NullTranscriber, Transcriber, Transcript
from .tts import ElevenLabsSpeaker, SilentSpeaker, Speaker

__all__ = [
    "Microphone",
    "list_input_devices",
    "AssemblyAIStreaming",
    "NullTranscriber",
    "Transcriber",
    "Transcript",
    "ElevenLabsSpeaker",
    "SilentSpeaker",
    "Speaker",
]
