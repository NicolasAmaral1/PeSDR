"""Deterministic fakes for tests + simulate CLI."""

from __future__ import annotations

from ai_sdr.voice.base import (
    SpeechSynthesizer,
    SpeechTranscriber,
    SynthesisResult,
    TranscriptionResult,
)


class FakeSynthesizer(SpeechSynthesizer):
    def __init__(self, content_type: str = "audio/ogg; codecs=opus") -> None:
        self._content_type = content_type
        self.calls: list[tuple[str, str, str | None]] = []

    async def synthesize(
        self, text: str, voice_id: str, *, emotion: str | None = None, fmt: str = "ogg_opus"
    ) -> SynthesisResult:
        self.calls.append((text, voice_id, emotion))
        return SynthesisResult(
            audio=b"FAKEOGG" + text.encode("utf-8"),
            content_type=self._content_type,
            voice_id=voice_id,
            char_count=len(text),
            duration_ms=len(text) * 60,
        )


class FakeTranscriber(SpeechTranscriber):
    def __init__(self, text: str = "transcrição fake", confidence: float = 0.95) -> None:
        self._text = text
        self._confidence = confidence
        self.calls: list[bytes] = []

    async def transcribe(self, audio: bytes, *, language: str = "pt-BR") -> TranscriptionResult:
        self.calls.append(audio)
        return TranscriptionResult(
            text=self._text, confidence=self._confidence, provider="fake", duration_ms=1000
        )
