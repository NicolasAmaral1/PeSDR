"""Tests for the whitelist guardrail validator."""

from __future__ import annotations

from ai_sdr.guardrails.whitelist import validate_whitelist
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig


def _guardrails(
    enabled: bool = True,
    prices: list[int] | None = None,
    products: list[str] | None = None,
) -> GuardrailsConfig:
    return GuardrailsConfig(
        enabled=enabled,
        allowed_prices=prices if prices is not None else [247, 1497, 6000],
        allowed_products=products if products is not None else ["Mentoria", "Aceleradora"],
        fallback_text="Confirmo já já, ok?",
    )


def test_disabled_is_noop() -> None:
    g = _guardrails(enabled=False, prices=[], products=[])
    v = validate_whitelist(
        prices_mentioned=[9999],
        products_mentioned=["Inexistente"],
        guardrails=g,
    )
    assert v.passed is True


def test_nothing_mentioned_passes() -> None:
    g = _guardrails()
    v = validate_whitelist(prices_mentioned=[], products_mentioned=[], guardrails=g)
    assert v.passed is True


def test_all_mentioned_within_whitelist_passes() -> None:
    g = _guardrails()
    v = validate_whitelist(
        prices_mentioned=[247, 1497],
        products_mentioned=["Mentoria"],
        guardrails=g,
    )
    assert v.passed is True


def test_price_outside_whitelist_fails_and_explains() -> None:
    g = _guardrails(prices=[247, 1497])
    v = validate_whitelist(prices_mentioned=[5000], products_mentioned=[], guardrails=g)
    assert v.passed is False
    assert v.reason is not None
    assert "5000" in v.reason
    assert v.suggested_fix is not None
    assert "247" in v.suggested_fix and "1497" in v.suggested_fix


def test_product_outside_whitelist_fails_case_insensitive() -> None:
    g = _guardrails(products=["Mentoria", "Aceleradora"])
    v = validate_whitelist(
        prices_mentioned=[],
        products_mentioned=["Coaching"],
        guardrails=g,
    )
    assert v.passed is False
    assert "Coaching" in v.reason  # type: ignore[operator]


def test_product_case_insensitive_match_passes() -> None:
    g = _guardrails(products=["Mentoria"])
    v = validate_whitelist(
        prices_mentioned=[],
        products_mentioned=["mentoria", "MENTORIA"],
        guardrails=g,
    )
    assert v.passed is True


def test_multiple_violations_aggregated_in_reason() -> None:
    g = _guardrails(prices=[247], products=["Mentoria"])
    v = validate_whitelist(prices_mentioned=[5000, 9999], products_mentioned=["X"], guardrails=g)
    assert v.passed is False
    assert "5000" in v.reason and "9999" in v.reason and "X" in v.reason  # type: ignore[operator]
