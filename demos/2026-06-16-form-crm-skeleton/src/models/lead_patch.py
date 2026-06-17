"""Patch demonstrativo do Lead ORM (delta da spec — não é o arquivo completo).

Esta é uma demonstração de QUE mudança vai ser feita em
`src/ai_sdr/models/lead.py` REAL. Não é pra ser importado — é referência
visual pra Nicolas ver o delta.

Mudança: adicionar coluna `crm_refs` JSONB.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# === SNIPPET pra adicionar em src/ai_sdr/models/lead.py ===


class _LeadPatch:
    """Pseudo-classe demonstrativa. NÃO IMPORTAR.

    Patch real em `models/lead.py`:

    Adicionar ao corpo da classe Lead (depois das colunas existentes):
    """

    # Esta é a única nova coluna que sai da migration 0030.
    crm_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="External CRM IDs and sync metadata, keyed by provider.",
    )

    # Estrutura esperada:
    # crm_refs = {
    #     "rdstation": {
    #         "contact_id": "abc123",
    #         "deal_id_mentoria": "def456",
    #         "deal_id_aceleradora": None,
    #         "last_synced_at": "2026-06-16T...",
    #     },
    #     # outros providers no futuro:
    #     # "hubspot": {...},
    # }


# === Helpers utilitários (podem ir pra repositories/lead_crm_refs.py) ===

# from uuid import UUID
# from sqlalchemy import select, update
# from sqlalchemy.dialects.postgresql import JSONB
# from sqlalchemy.ext.asyncio import AsyncSession
#
# async def get_crm_ref(
#     session: AsyncSession,
#     lead_id: UUID,
#     provider: str,
#     key: str,
# ) -> str | None:
#     """SELECT crm_refs -> 'rdstation' ->> 'contact_id' FROM leads WHERE id = :id"""
#     stmt = select(text(f"crm_refs -> :p ->> :k")).where(Lead.id == lead_id)
#     ...
#
# async def set_crm_ref(
#     session: AsyncSession,
#     lead_id: UUID,
#     provider: str,
#     key: str,
#     value: str,
# ) -> None:
#     """UPDATE leads SET crm_refs = jsonb_set(crm_refs, ARRAY[:p, :k], to_jsonb(:v))
#     WHERE id = :id
#     """
#     ...
