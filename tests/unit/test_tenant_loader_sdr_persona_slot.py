"""tenant_loader passes sdr_persona through as a raw dict slot."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest
import yaml

from ai_sdr.schemas.tenant_yaml import TenantConfig
from ai_sdr.tenant_loader.loader import TenantLoader


def _load_via_model_validate(yaml_text: str) -> TenantConfig:
    """Parse YAML and validate through TenantConfig directly."""
    data = yaml.safe_load(yaml_text)
    return TenantConfig.model_validate(data)


def test_no_sdr_persona_is_backward_compat() -> None:
    yaml_text = dedent("""
        id: test-tenant
        display_name: Test Tenant
        timezone: "America/Sao_Paulo"
        llm:
          default:
            provider: openai
            model: gpt-5-mini
            api_key_ref: secrets/openai_key
    """).strip()
    cfg = _load_via_model_validate(yaml_text)
    assert cfg.sdr_persona is None


def test_sdr_persona_passes_through_as_raw_dict() -> None:
    yaml_text = dedent("""
        id: test-tenant
        display_name: Test Tenant
        timezone: "America/Sao_Paulo"
        llm:
          default:
            provider: openai
            model: gpt-5-mini
            api_key_ref: secrets/openai_key
        sdr_persona:
          voice: |
            Tom PT-BR informal.
          conduct: |
            Sempre reconheca.
          examples: []
    """).strip()
    cfg = _load_via_model_validate(yaml_text)
    assert cfg.sdr_persona is not None
    assert "Tom PT-BR informal" in cfg.sdr_persona["voice"]
    assert "Sempre reconheca" in cfg.sdr_persona["conduct"]
    assert cfg.sdr_persona["examples"] == []
