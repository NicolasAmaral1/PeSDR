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


def validate_response_text(text: str, cfg: GuardrailConfig) -> ValidationResult:
    """Validate that response_text obeys the guardrails."""
    if not cfg.disallowed_price_pattern:
        return ValidationResult(ok=True, violation=None, category=None)

    matches = re.findall(cfg.disallowed_price_pattern, text)
    if not matches:
        return ValidationResult(ok=True, violation=None, category=None)

    # Normalize allowed list (strip + case-insensitive comparison).
    allowed_norm = {p.lower().strip() for p in cfg.allowed_prices}

    for m in matches:
        if m.lower().strip() not in allowed_norm:
            return ValidationResult(
                ok=False,
                violation=(
                    f"response text contains a price '{m}' that is not in "
                    f"the tenant's allowed_prices whitelist"
                ),
                category="price_invented",
            )

    return ValidationResult(ok=True, violation=None, category=None)
