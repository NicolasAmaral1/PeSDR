"""Lead assignment routes — pending list + assign-treeflow.

The CLI commands in `ai_sdr.cli.leads` consume these endpoints (not the
DB directly) so any future HITL UI (Plano 11) uses the same surface."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

import structlog
from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import arq_pool, db_session
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.loader import TreeFlowLoader
from ai_sdr.treeflow.runtime import TalkFlowRuntime

log = structlog.get_logger(__name__)
router = APIRouter()


class PendingLeadOut(BaseModel):
    id: uuid.UUID
    whatsapp_e164: str | None
    external_label: str | None
    status: str
    created_at: datetime
    queued_messages: int


class AssignBody(BaseModel):
    treeflow_id: str


class AssignOut(BaseModel):
    talkflow_id: uuid.UUID
    queued_messages_to_replay: int


async def _load_tenant(db: AsyncSession, slug: str) -> Tenant:
    t = (await db.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail=f"tenant {slug!r} not found")
    return t


@router.get(
    "/tenants/{tenant_slug}/leads/pending",
    response_model=list[PendingLeadOut],
)
async def list_pending_leads(
    tenant_slug: str,
    db: Annotated[AsyncSession, Depends(db_session)],
) -> list[PendingLeadOut]:
    tenant = await _load_tenant(db, tenant_slug)
    await set_tenant_context(db, tenant.id)

    # Lead + count of queued inbound_messages per lead (LEFT JOIN aggregate)
    queued_count_sq = (
        select(
            InboundMessageRow.lead_id,
            func.count().label("n"),
        )
        .where(InboundMessageRow.status == "queued")
        .group_by(InboundMessageRow.lead_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(Lead, queued_count_sq.c.n)
            .outerjoin(queued_count_sq, queued_count_sq.c.lead_id == Lead.id)
            .where(
                Lead.status == "pending_assignment",
                Lead.is_sandbox.is_(False),  # PR #24: skip sandbox leads
            )
            .order_by(Lead.created_at.desc())
        )
    ).all()
    return [
        PendingLeadOut(
            id=lead.id,
            whatsapp_e164=lead.whatsapp_e164,
            external_label=lead.external_label,
            status=lead.status,
            created_at=lead.created_at,
            queued_messages=int(n or 0),
        )
        for lead, n in rows
    ]


@router.post(
    "/tenants/{tenant_slug}/leads/{lead_id}/assign",
    response_model=AssignOut,
    status_code=202,
)
async def assign_lead(
    tenant_slug: str,
    lead_id: uuid.UUID,
    body: AssignBody,
    db: Annotated[AsyncSession, Depends(db_session)],
    pool: Annotated[ArqRedis, Depends(arq_pool)],
) -> AssignOut:
    tenant = await _load_tenant(db, tenant_slug)
    await set_tenant_context(db, tenant.id)
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail=f"lead {lead_id} not found")
    if lead.status != "pending_assignment":
        raise HTTPException(
            status_code=409,
            detail=f"lead is {lead.status}, not pending_assignment",
        )

    tdir = Path(get_settings().tenants_dir)
    runtime = TalkFlowRuntime(
        tenant_loader=TenantLoader(tdir),
        treeflow_loader=TreeFlowLoader(tdir),
        sops_loader=SopsLoader(tdir),
    )
    talkflow = await runtime.create(db, tenant, lead_id=lead.id, treeflow_id=body.treeflow_id)
    lead.status = "active"

    queued_count = (
        await db.execute(
            select(func.count(InboundMessageRow.id)).where(
                InboundMessageRow.lead_id == lead.id,
                InboundMessageRow.status == "queued",
            )
        )
    ).scalar_one()
    await db.commit()

    await pool.enqueue_job("process_lead_inbox", str(tenant.id), str(lead.id))
    log.info(
        "lead.assigned",
        tenant_slug=tenant_slug,
        lead_id=str(lead.id),
        treeflow_id=body.treeflow_id,
        queued=queued_count,
    )
    return AssignOut(
        talkflow_id=talkflow.id,
        queued_messages_to_replay=int(queued_count),
    )
