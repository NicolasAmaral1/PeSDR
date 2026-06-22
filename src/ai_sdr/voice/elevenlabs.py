"""ElevenLabs SpeechSynthesizer (TTS) + optional Scribe transcriber.

HTTP via httpx with bounded tenacity retry on 5xx/429. Synthesis returns
raw audio bytes; the caller stores them + sends via the messaging adapter.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx
import tenacity

from ai_sdr.schemas.tenant_yaml import SpeechSynthesisConfig, SpeechTranscriptionConfig
from ai_sdr.voice.base import (
    SpeechSynthesizer,
    SpeechTranscriber,
    SynthesisResult,
    TranscriptionResult,
)
from ai_sdr.voice.factory import register_synthesizer, register_transcriber

_OUTPUT_FORMAT = {"ogg_opus": "opus_48000", "mp3": "mp3_44100_128"}
_CONTENT_TYPE = {"ogg_opus": "audio/ogg; codecs=opus", "mp3": "audio/mpeg"}
_WAIT = tenacity.wait_exponential(multiplier=1, min=1, max=4)
_MAX_ATTEMPTS = 3


def _build_http_client(timeout: float) -> httpx.AsyncClient:  # test seam
    return httpx.AsyncClient(timeout=timeout)


class ElevenLabsSynthesizer(SpeechSynthesizer):
    def __init__(self, cfg: SpeechSynthesisConfig, secrets: Mapping[str, str]) -> None:
        self._api_key = secrets[cfg.credentials_ref.removeprefix("secrets/")]
        self._timeout = float(cfg.timeout_seconds)
        self._fmt = cfg.format

    async def synthesize(
        self, text: str, voice_id: str, *, emotion: str | None = None, fmt: str = "ogg_opus"
    ) -> SynthesisResult:
        out_fmt = _OUTPUT_FORMAT.get(fmt, "opus_48000")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format={out_fmt}"
        body = {"text": text, "model_id": "eleven_multilingual_v2"}
        headers = {"xi-api-key": self._api_key, "accept": "audio/ogg"}
        retryer = tenacity.AsyncRetrying(
            stop=tenacity.stop_after_attempt(_MAX_ATTEMPTS),
            wait=_WAIT,
            retry=tenacity.retry_if_exception_type(httpx.HTTPStatusError),
            reraise=True,
        )
        async for attempt in retryer:
            with attempt:
                async with _build_http_client(self._timeout) as client:
                    resp = await client.post(url, json=body, headers=headers)
                if resp.status_code >= 500 or resp.status_code == 429:
                    resp.raise_for_status()
                if resp.status_code != 200:
                    raise RuntimeError(f"elevenlabs synth failed: {resp.status_code} {resp.text}")
                return SynthesisResult(
                    audio=resp.content,
                    content_type=_CONTENT_TYPE.get(fmt, "audio/ogg; codecs=opus"),
                    voice_id=voice_id,
                    char_count=len(text),
                    duration_ms=None,
                )
        raise RuntimeError("unreachable: tenacity exhausted")


class ElevenLabsTranscriber(SpeechTranscriber):
    def __init__(self, cfg: SpeechTranscriptionConfig, secrets: Mapping[str, str]) -> None:
        self._api_key = secrets[cfg.credentials_ref.removeprefix("secrets/")]
        self._language = cfg.language

    async def transcribe(self, audio: bytes, *, language: str = "pt-BR") -> TranscriptionResult:
        url = "https://api.elevenlabs.io/v1/speech-to-text"
        headers = {"xi-api-key": self._api_key}
        files = {"file": ("audio.ogg", audio, "audio/ogg")}
        data = {"model_id": "scribe_v1"}
        async with _build_http_client(30.0) as client:
            resp = await client.post(url, headers=headers, files=files, data=data)
        resp.raise_for_status()
        payload = resp.json()
        return TranscriptionResult(
            text=payload.get("text", ""),
            confidence=float(payload.get("language_probability", 1.0) or 1.0),
            provider="elevenlabs",
            duration_ms=None,
        )


@register_synthesizer("elevenlabs")
def _build_synth(cfg: SpeechSynthesisConfig, secrets: Mapping[str, str]) -> SpeechSynthesizer:
    return ElevenLabsSynthesizer(cfg, secrets)


@register_transcriber("elevenlabs")
def _build_transcriber(
    cfg: SpeechTranscriptionConfig, secrets: Mapping[str, str]
) -> SpeechTranscriber:
    return ElevenLabsTranscriber(cfg, secrets)
