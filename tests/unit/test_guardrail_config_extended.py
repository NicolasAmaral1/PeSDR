"""GuardrailConfig has allowed_products + fallback_text (FE-03a Task 8)."""

from __future__ import annotations

from ai_sdr.guardrails.validator import GuardrailConfig


def test_allowed_products_field_exists():
    cfg = GuardrailConfig(
        disallowed_price_pattern=r"R\$\s*\d+",
        allowed_prices=["R$ 6000"],
        allowed_products=["Mentoria", "Aceleradora"],
        fallback_text="Deixa eu confirmar isso com a equipe.",
    )
    assert cfg.allowed_products == ["Mentoria", "Aceleradora"]


def test_fallback_text_field_exists():
    cfg = GuardrailConfig(
        disallowed_price_pattern="",
        allowed_prices=[],
        allowed_products=[],
        fallback_text="Vou validar com a equipe.",
    )
    assert cfg.fallback_text == "Vou validar com a equipe."
