"""Unit tests for _build_voice_stack helper in the inbound worker drain."""

from __future__ import annotations

import pytest

from ai_sdr.schemas.tenant_yaml import (
    SpeechSynthesisConfig,
    SpeechTranscriptionConfig,
    StorageConfig,
    VoiceConfig,
)
from ai_sdr.worker.jobs.inbound import _build_voice_stack


def test_build_voice_stack_returns_none_when_no_voice():
    synth, trans, storage = _build_voice_stack(_cfg_without_voice(), {})
    assert (synth, trans, storage) == (None, None, None)


def test_build_voice_stack_builds_all_three():
    cfg = _cfg_with_voice()
    secrets = {
        "elevenlabs_api_key": "k",
        "minio_endpoint": "https://m",
        "minio_access_key": "a",
        "minio_secret_key": "s",
    }
    synth, trans, storage = _build_voice_stack(cfg, secrets)
    assert synth is not None and trans is not None and storage is not None


def _cfg_without_voice():
    from ai_sdr.schemas.tenant_yaml import TenantConfig

    return TenantConfig(id="avelum", display_name="A", timezone="America/Sao_Paulo")


def _cfg_with_voice():
    from ai_sdr.schemas.tenant_yaml import TenantConfig

    return TenantConfig(
        id="avelum",
        display_name="A",
        timezone="America/Sao_Paulo",
        voice=VoiceConfig(
            response_mode="match_lead",
            synthesis=SpeechSynthesisConfig(
                provider="elevenlabs",
                credentials_ref="secrets/elevenlabs_api_key",
                voice_id="v1",
            ),
            transcription=SpeechTranscriptionConfig(
                provider="elevenlabs",
                credentials_ref="secrets/elevenlabs_api_key",
            ),
        ),
        storage=StorageConfig(
            provider="minio",
            bucket="b",
            endpoint_ref="secrets/minio_endpoint",
            access_key_ref="secrets/minio_access_key",
            secret_key_ref="secrets/minio_secret_key",
        ),
    )
