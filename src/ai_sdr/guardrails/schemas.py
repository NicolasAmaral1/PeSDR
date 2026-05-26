"""Verdict — the typed output of all guardrail validators (whitelist + critic)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Verdict(BaseModel):
    """A guardrail decision.

    Pydantic BaseModel (not dataclass) because critic_pass uses LangChain's
    `with_structured_output(Verdict)`, which requires a Pydantic class.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str | None = None
    suggested_fix: str | None = None
