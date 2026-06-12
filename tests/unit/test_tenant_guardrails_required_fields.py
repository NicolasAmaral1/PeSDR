"""GuardrailsConfig requires allowed_products + fallback_text when enabled (FE-03a Task 10)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.schemas.tenant_yaml import GuardrailsConfig


def test_enabled_without_allowed_products_raises():
    with pytest.raises(ValidationError, match="allowed_products"):
        GuardrailsConfig(
            enabled=True,
            allowed_prices=[6000],
            allowed_products=[],
            fallback_text="Deixa eu confirmar com a equipe.",
        )


def test_enabled_without_fallback_text_raises():
    with pytest.raises(ValidationError, match="fallback_text"):
        GuardrailsConfig(
            enabled=True,
            allowed_prices=[6000],
            allowed_products=["Mentoria"],
            fallback_text="",
        )


def test_fallback_text_under_min_length_raises():
    with pytest.raises(ValidationError, match="fallback_text"):
        GuardrailsConfig(
            enabled=True,
            allowed_prices=[6000],
            allowed_products=["Mentoria"],
            fallback_text="short",  # < 10 chars
        )


def test_disabled_does_not_require_lists():
    cfg = GuardrailsConfig(
        enabled=False,
        allowed_prices=[],
        allowed_products=[],
        fallback_text="",
    )
    assert cfg.enabled is False
