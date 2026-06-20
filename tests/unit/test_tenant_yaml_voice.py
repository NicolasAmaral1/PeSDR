# tests/unit/test_tenant_yaml_voice.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.schemas.tenant_yaml import (
    SpeechSynthesisConfig,
    StorageConfig,
    TenantConfig,
    VoiceConfig,
)


def _base_tenant(**extra) -> dict:
    return {
        "id": "avelum",
        "display_name": "Avelum",
        "timezone": "America/Sao_Paulo",
        **extra,
    }


def test_voice_defaults_to_never_and_no_synthesis():
    v = VoiceConfig()
    assert v.response_mode == "never"
    assert v.fallback_to_text_on_failure is True
    assert v.synthesis is None


def test_voice_requires_synthesis_when_mode_not_never():
    with pytest.raises(ValidationError, match="synthesis"):
        VoiceConfig(response_mode="always")


def test_synthesis_ref_must_use_secrets_prefix():
    with pytest.raises(ValidationError, match="secrets/"):
        SpeechSynthesisConfig(
            provider="elevenlabs", credentials_ref="elevenlabs_key", voice_id="v1"
        )


def test_storage_ref_must_use_secrets_prefix():
    with pytest.raises(ValidationError, match="secrets/"):
        StorageConfig(provider="minio", bucket="b", endpoint_ref="minio")


def test_tenant_accepts_full_voice_and_storage_block():
    cfg = TenantConfig.model_validate(
        _base_tenant(
            voice={
                "response_mode": "match_lead",
                "synthesis": {
                    "provider": "elevenlabs",
                    "credentials_ref": "secrets/elevenlabs_api_key",
                    "voice_id": "ABC123",
                },
                "transcription": {
                    "provider": "elevenlabs",
                    "credentials_ref": "secrets/elevenlabs_api_key",
                },
            },
            storage={
                "provider": "minio",
                "bucket": "avelum-media",
                "endpoint_ref": "secrets/minio_endpoint",
                "access_key_ref": "secrets/minio_access_key",
                "secret_key_ref": "secrets/minio_secret_key",
            },
        )
    )
    assert cfg.voice.response_mode == "match_lead"
    assert cfg.voice.synthesis.voice_id == "ABC123"
    assert cfg.storage.bucket == "avelum-media"


def test_tenant_without_voice_is_text_only():
    cfg = TenantConfig.model_validate(_base_tenant())
    assert cfg.voice is None
    assert cfg.storage is None
