"""Canonical (vendor-neutral) data shapes for CRM operations.

These are the shapes that handlers receive after Jinja2 templating. They
are intentionally minimal — vendor-specific custom fields go through
`custom_fields` (a dict that the backend maps to the vendor's field IDs
via `tenant.yaml > crm.<provider>.custom_field_mapping`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DealStage = Literal["open", "won", "lost"]


class ContactCanonical(BaseModel):
    """A contact (a person) in the canonical vocabulary."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)  # E.164
    custom_fields: dict[str, str] = Field(default_factory=dict)


class DealCanonical(BaseModel):
    """A deal in the canonical vocabulary."""

    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1)
    stage: DealStage = "open"
    value: float | None = None
    currency: str = "BRL"
    qualification_notes: str | None = None
    custom_fields: dict[str, str] = Field(default_factory=dict)
