"""Python guardrails validator (replaces critic LLM for v2 path)."""

from __future__ import annotations

import pytest

from ai_sdr.guardrails.validator import GuardrailConfig, validate_response_text


def cfg(
    *,
    disallowed: str = r"R\$\s?\d+",
    allowed: list[str] | None = None,
) -> GuardrailConfig:
    return GuardrailConfig(
        disallowed_price_pattern=disallowed,
        allowed_prices=allowed or [],
    )


def test_clean_text_is_ok() -> None:
    r = validate_response_text("Oi! Vamos conversar?", cfg())
    assert r.ok is True
    assert r.violation is None


def test_price_mention_without_whitelist_is_violation() -> None:
    r = validate_response_text("O investimento e R$ 2000 por mes.", cfg())
    assert r.ok is False
    assert r.violation is not None
    assert "R$" in r.violation
    assert r.category == "price_invented"


def test_price_mention_in_whitelist_is_ok() -> None:
    r = validate_response_text(
        "Pelo nosso plano basico, R$ 297 por mes.",
        cfg(allowed=["R$ 297"]),
    )
    assert r.ok is True


def test_multiple_prices_one_invalid_is_violation() -> None:
    r = validate_response_text(
        "Temos planos de R$ 297 e R$ 5000.",
        cfg(allowed=["R$ 297"]),
    )
    assert r.ok is False
    assert "R$ 5000" in r.violation


def test_disallowed_pattern_can_be_disabled() -> None:
    r = validate_response_text("Diga R$ 2000.", cfg(disallowed=""))
    assert r.ok is True


def test_pattern_with_thousands_separator() -> None:
    r = validate_response_text(
        "Custa R$1.500/mes.",
        cfg(disallowed=r"R\$\s?[\d\.]+", allowed=["R$ 297"]),
    )
    assert r.ok is False


def test_validation_result_is_immutable_like() -> None:
    """Just a smoke check that fields exist for downstream handlers."""
    r = validate_response_text("anything", cfg())
    # Must expose ok, violation, category — used by Task 10 retry logic.
    assert hasattr(r, "ok")
    assert hasattr(r, "violation")
    assert hasattr(r, "category")
