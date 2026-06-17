"""Vocabulário canônico interno do CRM.

Esses Pydantic models são o "language" que o PeSDR fala internamente. Cada
backend traduz pra vocabulário do vendor (RD Station, HubSpot, etc).

Decisão (ADR CRM §canônico mínimo): nada vinculado a vendor — `stage` é
`open|won|lost` em todos, independente de como cada vendor chama.

Custom fields NÃO entram no canônico — vivem em `custom_fields: dict[str, str]`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# Stages canônicos — vocabulário PeSDR interno
DealStage = Literal["open", "won", "lost"]


class ContactCanonical(BaseModel):
    """Contact no vocabulário interno PeSDR.

    Backend traduz pra:
    - RD Station: contact object com phones (cellphone type), emails
    - HubSpot: contact properties firstname/lastname/phone/email
    - Pipedrive: person object com phones/emails arrays
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(
        default_factory=list,
        description="E.164 format (e.g., +5511987654321)",
    )
    custom_fields: dict[str, str] = Field(
        default_factory=dict,
        description="Tradução pra IDs vendor-specific feita pelo backend via "
        "tenant.yaml > crm.<provider>.custom_field_mapping",
    )


class DealCanonical(BaseModel):
    """Deal no vocabulário interno PeSDR.

    Idempotência: backends deduplicam por (lead_id, product). Bumps de stage
    são update — não criar novos deals pra mesmo product no mesmo lead.
    """

    model_config = ConfigDict(extra="forbid")

    product: str = Field(
        min_length=1,
        description="Nome canônico do produto. Pra Manoela: "
        "'Mentoria' | 'Aceleradora' | 'Downsell'.",
    )
    stage: DealStage = "open"
    value: float | None = Field(
        default=None,
        description="Valor em moeda local (default BRL pra piloto Manoela)",
    )
    currency: str = "BRL"
    qualification_notes: str | None = Field(
        default=None,
        description="Resumo da qualificação coletada pela conversa. "
        "Backend pode mandar como nota anexada ao deal/contact.",
    )
    custom_fields: dict[str, str] = Field(default_factory=dict)
