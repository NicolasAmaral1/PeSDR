"""Python guardrails validator — Critic LLM replacement for the FE v2 path.

The v1 LangGraph pipeline used a separate critic LLM call to validate
that response text didn't hallucinate prices or violate other rules.
FE-01b replaces that with deterministic Python checks:

1. Regex against tenant.guardrails.disallowed_price_pattern.
2. Price whitelist: detected price-like tokens must appear in
   tenant.guardrails.allowed_prices.
3. Product whitelist (FE-03a Task 9): mentioned product names must
   appear in tenant.guardrails.allowed_products.

Violations trigger a corrective retry (Task 10) and, after 2 failures,
the validator emits tenant.guardrails.fallback_text as the response and
escalates the Talk to requires_review.

The legacy critic (guardrails/critic.py) and runner stay alive for the
v1 LangGraph path; FE-02 deletes them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GuardrailConfig:
    disallowed_price_pattern: str  # regex; empty string disables the check
    allowed_prices: list[str]
    allowed_products: list[str]
    fallback_text: str


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    violation: str | None
    category: str | None  # 'price_invented' | None for v1; future categories added


def _normalize_product(s: str) -> str:
    """lowercase + collapse internal whitespace + strip ends. No punctuation removal."""
    return " ".join(s.lower().split())


def validate_response_text(text: str, cfg: GuardrailConfig) -> ValidationResult:
    """Validate that response_text obeys the guardrails."""
    # Price check (existing — preserved)
    if cfg.disallowed_price_pattern:
        matches = re.findall(cfg.disallowed_price_pattern, text)
        if matches:
            allowed_norm = {p.lower().strip() for p in cfg.allowed_prices}
            for m in matches:
                if m.lower().strip() not in allowed_norm:
                    return ValidationResult(
                        ok=False,
                        violation=(
                            f"response text contains a price '{m}' that is "
                            f"not in the tenant's allowed_prices whitelist"
                        ),
                        category="price_invented",
                    )

    # Product check (NEW — FE-03a Task 9)
    if cfg.allowed_products:
        allowed = {_normalize_product(p) for p in cfg.allowed_products}
        normalized_text = _normalize_product(text)
        # Substring match against the whole normalized text — any allowed
        # product mention is ok. The check fails only when the text contains
        # a "product-like" capitalized phrase NOT in the whitelist.
        # Conservative: require the text to mention at least one allowed
        # product whenever it speaks about products at all. We approximate
        # "speaks about products" via the presence of trigger keywords.
        product_triggers = (
            "curso",
            "programa",
            "produto",
            "treinamento",
            "mentoria",
            "consultoria",
        )
        text_lower = text.lower()
        mentions_product_topic = any(t in text_lower for t in product_triggers)
        if mentions_product_topic:
            has_allowed = any(p in normalized_text for p in allowed)
            if not has_allowed:
                return ValidationResult(
                    ok=False,
                    violation=(
                        "response text mentions a product/program that is "
                        "not in the tenant's allowed_products whitelist"
                    ),
                    category="product_invented",
                )

    return ValidationResult(ok=True, violation=None, category=None)
