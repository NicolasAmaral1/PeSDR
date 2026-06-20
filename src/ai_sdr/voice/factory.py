"""Dispatch synthesizers + transcribers by provider name."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ai_sdr.schemas.tenant_yaml import SpeechSynthesisConfig, SpeechTranscriptionConfig
from ai_sdr.voice.base import SpeechSynthesizer, SpeechTranscriber
from ai_sdr.voice.fake import FakeSynthesizer, FakeTranscriber

_SYNTH: dict[str, Callable[[SpeechSynthesisConfig, Mapping[str, str]], SpeechSynthesizer]] = {}
_TRANS: dict[str, Callable[[SpeechTranscriptionConfig, Mapping[str, str]], SpeechTranscriber]] = {}


def register_synthesizer(name: str):
    def _wrap(builder):
        if name in _SYNTH:
            raise RuntimeError(f"synthesizer already registered: {name}")
        _SYNTH[name] = builder
        return builder

    return _wrap


def register_transcriber(name: str):
    def _wrap(builder):
        if name in _TRANS:
            raise RuntimeError(f"transcriber already registered: {name}")
        _TRANS[name] = builder
        return builder

    return _wrap


@register_synthesizer("fake")
def _fake_synth(cfg, secrets) -> SpeechSynthesizer:
    return FakeSynthesizer()


@register_transcriber("fake")
def _fake_trans(cfg, secrets) -> SpeechTranscriber:
    return FakeTranscriber()


def _ensure_elevenlabs(provider: str) -> None:
    if provider == "elevenlabs" and "elevenlabs" not in _SYNTH:
        from ai_sdr.voice import elevenlabs  # noqa: F401


def build_synthesizer(
    cfg: SpeechSynthesisConfig, secrets: Mapping[str, str]
) -> SpeechSynthesizer:
    _ensure_elevenlabs(cfg.provider)
    builder = _SYNTH.get(cfg.provider)
    if builder is None:
        raise ValueError(f"unknown synthesizer provider: {cfg.provider!r}")
    return builder(cfg, secrets)


def build_transcriber(
    cfg: SpeechTranscriptionConfig, secrets: Mapping[str, str]
) -> SpeechTranscriber:
    _ensure_elevenlabs(cfg.provider)
    builder = _TRANS.get(cfg.provider)
    if builder is None:
        raise ValueError(f"unknown transcriber provider: {cfg.provider!r}")
    return builder(cfg, secrets)
