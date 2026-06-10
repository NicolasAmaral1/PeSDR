"""validate_response_text catches unknown products (FE-03a Task 9)."""

from __future__ import annotations

from ai_sdr.guardrails.validator import GuardrailConfig, validate_response_text


def _cfg(products: list[str]) -> GuardrailConfig:
    return GuardrailConfig(
        disallowed_price_pattern="",
        allowed_prices=[],
        allowed_products=products,
        fallback_text="Vou validar com a equipe.",
    )


def test_allowed_product_passes():
    cfg = _cfg(["Mentoria", "Aceleradora"])
    r = validate_response_text("A Mentoria vai te ajudar.", cfg)
    assert r.ok


def test_unknown_product_fails():
    cfg = _cfg(["Mentoria"])
    r = validate_response_text("Vou te indicar o Curso Express.", cfg)
    assert not r.ok
    assert r.category == "product_invented"


def test_match_is_case_insensitive():
    cfg = _cfg(["Mentoria"])
    r = validate_response_text("a mentoria é boa", cfg)
    assert r.ok


def test_match_collapses_internal_whitespace():
    cfg = _cfg(["Mentoria Premium"])
    r = validate_response_text("A Mentoria  Premium é top.", cfg)
    assert r.ok


def test_empty_allowed_products_disables_check():
    cfg = _cfg([])
    r = validate_response_text("Vou te oferecer qualquer coisa aleatória.", cfg)
    assert r.ok
