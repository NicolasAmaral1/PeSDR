"""Console inbox API — contact-based read endpoints.

Routes (all behind require_tenant_access, tenant_slug in path for RLS):
  GET  /api/console/tenants/{tenant_slug}/instances
  GET  /api/console/tenants/{tenant_slug}/instances/{instance_id}/contacts
  GET  /api/console/tenants/{tenant_slug}/contacts/{lead_id}
  GET  /api/console/tenants/{tenant_slug}/contacts/{lead_id}/messages
  POST /api/console/tenants/{tenant_slug}/contacts/{lead_id}/read       -> 204

Tenant safety: every route passes tenant.id explicitly to the repository.
The messages, detail, and read routes verify lead.tenant_id == tenant.id
(404 on mismatch) before any data access.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import db_session
from ai_sdr.api.schemas.console_inbox import (
    ContactDetailOut,
    ContactOut,
    InstanceOut,
    MessageOut,
    ReadBody,
)
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.instance import Instance
from ai_sdr.models.lead import Lead
from ai_sdr.models.operator_read_marker import OperatorReadMarker
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.repositories.inbox_repository import (
    derive_state,
    list_contacts,
    list_messages,
)
from ai_sdr.web.auth import require_tenant_access

router = APIRouter(prefix="/api/console/tenants/{tenant_slug}")

TenantCtx = Annotated[tuple[Tenant, User], Depends(require_tenant_access)]
DbSession = Annotated[AsyncSession, Depends(db_session)]


# ---------------------------------------------------------------------------
# GET /instances
# ---------------------------------------------------------------------------

@router.get("/instances", response_model=list[InstanceOut])
async def list_instances(
    ctx: TenantCtx,
    db: DbSession,
) -> list[InstanceOut]:
    tenant, _user = ctx
    rows = (
        await db.execute(
            select(Instance).where(Instance.tenant_id == tenant.id)
        )
    ).scalars().all()
    return [
        InstanceOut(
            id=inst.id,
            channel_label=inst.channel_label,
            display_name=inst.display_name,
            phone_e164=inst.phone_e164,
        )
        for inst in rows
    ]


# ---------------------------------------------------------------------------
# GET /instances/{instance_id}/contacts
# ---------------------------------------------------------------------------

@router.get("/instances/{instance_id}/contacts", response_model=list[ContactOut])
async def list_instance_contacts(
    instance_id: uuid.UUID,
    ctx: TenantCtx,
    db: DbSession,
    status: Annotated[str | None, Query()] = None,
    funnel: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    before: Annotated[datetime | None, Query()] = None,
) -> list[ContactOut]:
    tenant, user = ctx

    instance = (
        await db.execute(
            select(Instance).where(
                Instance.id == instance_id,
                Instance.tenant_id == tenant.id,
            )
        )
    ).scalar_one_or_none()
    if instance is None:
        raise HTTPException(status_code=404, detail=f"instance {instance_id} not found")

    contacts = await list_contacts(
        db,
        tenant_id=tenant.id,
        channel_label=instance.channel_label,
        user_id=user.id,
        status=status,
        funnel=funnel,
        q=q,
        limit=50,
        before=before,
    )
    return [
        ContactOut(
            lead_id=c.lead_id,
            display_name=c.display_name,
            whatsapp_e164=c.whatsapp_e164,
            last_message_at=c.last_message_at,
            last_message_preview=c.last_message_preview,
            state=c.state,  # type: ignore[arg-type]
            funnel_node=c.funnel_node,
            unread=c.unread,
        )
        for c in contacts
    ]


# ---------------------------------------------------------------------------
# GET /contacts/{lead_id}
# ---------------------------------------------------------------------------

@router.get("/contacts/{lead_id}", response_model=ContactDetailOut)
async def get_contact_detail(
    lead_id: uuid.UUID,
    ctx: TenantCtx,
    db: DbSession,
) -> ContactDetailOut:
    tenant, _user = ctx

    lead = (
        await db.execute(
            select(Lead).where(Lead.id == lead_id)
        )
    ).scalar_one_or_none()
    if lead is None or lead.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail=f"contact {lead_id} not found")

    # Compute 24h messaging window from last inbound
    last_inbound_at: datetime | None = (
        await db.execute(
            select(InboundMessageRow.received_at)
            .where(
                InboundMessageRow.lead_id == lead_id,
                InboundMessageRow.tenant_id == tenant.id,
            )
            .order_by(InboundMessageRow.received_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    window_expires_at: datetime | None = None
    window_open: bool = False
    if last_inbound_at is not None:
        window_expires_at = last_inbound_at + timedelta(hours=24)
        window_open = datetime.now(UTC) < window_expires_at

    # Load the lead's active talk (most recent active/requires_review)
    active_talk: Talk | None = (
        await db.execute(
            select(Talk)
            .where(
                Talk.lead_id == lead_id,
                Talk.tenant_id == tenant.id,
                Talk.status.in_(("active", "requires_review")),
            )
            .order_by(Talk.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    state = derive_state(active_talk)
    active_talk_id = active_talk.id if active_talk else None

    # Derive funnel_node from TalkFlowState.current_node, fall back to treeflow_id
    funnel_node: str | None = None
    if active_talk is not None:
        funnel_node = (
            await db.execute(
                select(TalkFlowState.current_node)
                .where(TalkFlowState.talk_id == active_talk.id)
            )
        ).scalar_one_or_none()
        if funnel_node is None:
            funnel_node = active_talk.treeflow_id

    return ContactDetailOut(
        lead_id=lead.id,
        display_name=lead.display_name,
        whatsapp_e164=lead.whatsapp_e164,
        state=state,  # type: ignore[arg-type]
        funnel_node=funnel_node,
        active_talk_id=active_talk_id,
        ai_reasoning=None,
        window_open=window_open,
        window_expires_at=window_expires_at,
    )


# ---------------------------------------------------------------------------
# GET /contacts/{lead_id}/messages
# ---------------------------------------------------------------------------

@router.get("/contacts/{lead_id}/messages", response_model=list[MessageOut])
async def list_contact_messages(
    lead_id: uuid.UUID,
    ctx: TenantCtx,
    db: DbSession,
    before: Annotated[datetime | None, Query()] = None,
) -> list[MessageOut]:
    tenant, _user = ctx

    lead = (
        await db.execute(
            select(Lead).where(Lead.id == lead_id)
        )
    ).scalar_one_or_none()
    if lead is None or lead.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail=f"contact {lead_id} not found")

    messages = await list_messages(db, lead_id=lead_id, before=before)
    return [
        MessageOut(
            id=m.id,
            direction="in" if m.direction == "inbound" else "out",
            origin=(
                "lead"
                if m.direction == "inbound"
                else ("operator" if m.triggered_by == "operator" else "ai")
            ),
            text=m.text,
            media_type=m.media_type,
            audio_url=m.audio_url,
            transcription=None,
            at=m.created_at,
        )
        for m in messages
    ]


# ---------------------------------------------------------------------------
# POST /contacts/{lead_id}/read  -> 204
# ---------------------------------------------------------------------------

@router.post("/contacts/{lead_id}/read", status_code=204)
async def mark_contact_read(
    lead_id: uuid.UUID,
    body: ReadBody,
    ctx: TenantCtx,
    db: DbSession,
) -> Response:
    tenant, user = ctx

    lead = (
        await db.execute(
            select(Lead).where(Lead.id == lead_id)
        )
    ).scalar_one_or_none()
    if lead is None or lead.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail=f"contact {lead_id} not found")

    now = datetime.now(UTC)
    stmt = (
        pg_insert(OperatorReadMarker)
        .values(
            user_id=user.id,
            lead_id=lead_id,
            tenant_id=tenant.id,
            last_read_at=now,
            last_read_message_at=body.last_read_message_at,
        )
        .on_conflict_do_update(
            index_elements=["user_id", "lead_id"],
            set_={
                "last_read_at": now,
                "last_read_message_at": body.last_read_message_at,
                "tenant_id": tenant.id,
            },
        )
    )
    await db.execute(stmt)
    await db.commit()
    return Response(status_code=204)
