"""Voice adapter contracts — split into two narrow protocols so the STT
provider can differ from (and be swapped without touching) the TTS one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SynthesisResult:
    audio: bytes
    content_type: str
    voice_id: str
    char_count: int
    duration_ms: int | None = None


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    confidence: float
    provider: str
    duration_ms: int | None = None


class SpeechSynthesizer(ABC):
    @abstractmethod
    async def synthesize(
        self, text: str, voice_id: str, *, emotion: str | None = None, fmt: str = "ogg_opus"
    ) -> SynthesisResult: ...


class SpeechTranscriber(ABC):
    @abstractmethod
    async def transcribe(self, audio: bytes, *, language: str = "pt-BR") -> TranscriptionResult: ...
