"""Helpers de ingestão: resolução de Lead + criação de Talk a partir do form.

Não confundir com `messaging/ingest.py` (mensagens WhatsApp). Mesma família de
helpers, escopo diferente.

Em Plano 6 (IdentityResolver), `find_or_create_lead_by_form` e
`find_or_create_lead_by_address` se unificam num único `IdentityResolver.resolve_inbound`.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.forms.base import LeadIdentifier
from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk  # FlowEngine v2
from ai_sdr.models.talkflow_state import TalkFlowState  # FlowEngine v2
from ai_sdr.schemas.tenant_yaml import TenantConfig
from typing import Any


async def find_or_create_lead_by_form(
    session: AsyncSession,
    tenant: TenantConfig,
    identifier: LeadIdentifier,
) -> Lead:
    """Resolve Lead a partir de LeadIdentifier.

    Estratégia (conservadora — vide ADR CRM §riscos):
    1. Se whatsapp_e164 presente: SELECT por (tenant_id, whatsapp_e164).
       Se exists → reuse.
       Se não → create Lead novo com status='active'
                (vindo de form, lead foi explicitamente captado).
    2. Se whatsapp_e164 ausente: log warning, raise (fluxo degradado).

    Email NÃO é usado pra matching no piloto. Plano 6 generaliza.

    Args:
        session: AsyncSession com tenant_context já setado.
        tenant: TenantConfig.
        identifier: LeadIdentifier validado pelo adapter.

    Returns:
        Lead row (criado ou achado).

    Raises:
        ValueError: se identifier não tem whatsapp_e164 (caso degradado).
    """
    raise NotImplementedError("Fase A T6 — find_or_create_lead_by_form")


async def create_talk_with_state(
    session: AsyncSession,
    tenant: TenantConfig,
    lead: Lead,
    treeflow_id: str,
    preloaded_collected: dict[str, Any],
) -> Talk:
    """Cria Talk + TalkFlowState pré-populado com campos do form.

    Resolve TreeflowVersion pra tenant + treeflow_id (mais recente publicada),
    cria Talk com:
        - status='active'
        - treeflow_version_id=<resolved>
        - lead_id=lead.id
        - handling_mode='ai' (default)

    Cria TalkFlowState com:
        - talk_id=<created>
        - current_node=<treeflow.entry_node>
        - collected=preloaded_collected (campos do form pré-populados)
        - messages=[]
        - turn_count=0

    Args:
        session: AsyncSession com tenant_context setado.
        tenant: TenantConfig.
        lead: Lead resolvido por find_or_create_lead_by_form.
        treeflow_id: ID do TreeFlow inicial (de tenant.yaml > forms.<provider>.start_treeflow).
        preloaded_collected: campos extraídos do form.

    Returns:
        Talk criado (com state attached na sessão).
    """
    raise NotImplementedError("Fase A T6 — create_talk_with_state")
