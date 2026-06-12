"""TenantConfig.humanization parsing + bounds (FE-03b Task 7)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.schemas.tenant_yaml import HumanizationConfig


def test_defaults_are_sensible():
    cfg = HumanizationConfig()
    assert cfg.enabled is True
    assert cfg.chunk_delimiter == "\n\n"
    assert cfg.chars_per_second_min == 8.0
    assert cfg.chars_per_second_max == 15.0
    assert cfg.min_delay_ms == 800
    assert cfg.max_delay_ms == 4000
    assert cfg.apply_to_voice is False


def test_accepts_valid_override():
    cfg = HumanizationConfig(
        enabled=False,
        chars_per_second_min=5.0,
        chars_per_second_max=20.0,
        min_delay_ms=200,
        max_delay_ms=8000,
    )
    assert cfg.enabled is False
    assert cfg.chars_per_second_min == 5.0


def test_rejects_chars_per_second_min_greater_than_max():
    with pytest.raises(ValidationError, match="chars_per_second_min"):
        HumanizationConfig(
            chars_per_second_min=20.0,
            chars_per_second_max=10.0,
        )


def test_rejects_min_delay_greater_than_max_delay():
    with pytest.raises(ValidationError, match="min_delay_ms"):
        HumanizationConfig(min_delay_ms=5000, max_delay_ms=1000)


def test_rejects_negative_chars_per_second():
    with pytest.raises(ValidationError):
        HumanizationConfig(chars_per_second_min=-1.0, chars_per_second_max=10.0)


def test_rejects_negative_delay_ms():
    with pytest.raises(ValidationError):
        HumanizationConfig(min_delay_ms=-100, max_delay_ms=1000)
