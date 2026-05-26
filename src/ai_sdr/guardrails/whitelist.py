"""Whitelist validator — checks LLM-emitted price/product mentions against tenant allowlists."""

from __future__ import annotations

from ai_sdr.guardrails.schemas import Verdict
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig


def validate_whitelist(
    prices_mentioned: list[int],
    products_mentioned: list[str],
    guardrails: GuardrailsConfig,
) -> Verdict:
    """Return Verdict(passed=True) when guardrails are off or nothing violates.

    Otherwise return Verdict(passed=False) with a human-readable reason and a
    `suggested_fix` message intended to be injected back into the LLM as a
    SystemMessage on retry.
    """
    if not guardrails.enabled:
        return Verdict(passed=True)

    allowed_products_lower = {p.lower() for p in guardrails.allowed_products}
    bad_prices = sorted({p for p in prices_mentioned if p not in guardrails.allowed_prices})
    bad_products = sorted(
        {p for p in products_mentioned if p.lower() not in allowed_products_lower}
    )

    if not bad_prices and not bad_products:
        return Verdict(passed=True)

    parts: list[str] = []
    if bad_prices:
        parts.append(f"valor(es) não autorizado(s): {bad_prices}")
    if bad_products:
        parts.append(f"produto(s) não autorizado(s): {bad_products}")
    reason = "; ".join(parts)

    suggested_fix = (
        f"Sua resposta mencionou {reason}. "
        f"Valores permitidos: {guardrails.allowed_prices}. "
        f"Produtos permitidos: {guardrails.allowed_products}. "
        f"Refaça a resposta SEM mencionar valores ou produtos fora dessas listas."
    )

    return Verdict(passed=False, reason=reason, suggested_fix=suggested_fix)
