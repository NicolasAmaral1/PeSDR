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

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import adapter_registry, db_session
from ai_sdr.api.schemas.console_inbox import (
    ContactDetailOut,
    ContactOut,
    InstanceOut,
    MessageOut,
    ReadBody,
    SendBody,
)
from ai_sdr.db.advisory_lock import acquire_lead_lock
from ai_sdr.messaging.errors import TerminalError, WindowExpiredError
from ai_sdr.messaging.registry import AdapterRegistry
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.instance import Instance
from ai_sdr.models.lead import Lead
from ai_sdr.models.operator_read_marker import OperatorReadMarker
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.realtime.events import publish_inbox_event
from ai_sdr.realtime.producers import publish_message_created, resolve_instance_id
from ai_sdr.repositories.inbox_repository import (
    derive_state,
    list_contacts,
    list_messages,
)
from ai_sdr.web.auth import require_tenant_access

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/console/tenants/{tenant_slug}")

TenantCtx = Annotated[tuple[Tenant, User], Depends(require_tenant_access)]
DbSession = Annotated[AsyncSession, Depends(db_session)]
RegistryDep = Annotated[AdapterRegistry, Depends(adapter_registry)]


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


# ---------------------------------------------------------------------------
# POST /contacts/{lead_id}/takeover  -> 200 | 404 | 409
# ---------------------------------------------------------------------------

@router.post("/contacts/{lead_id}/takeover")
async def takeover_talk(
    lead_id: uuid.UUID,
    request: Request,
    ctx: TenantCtx,
    db: DbSession,
) -> dict:
    tenant, user = ctx

    lead = (
        await db.execute(select(Lead).where(Lead.id == lead_id))
    ).scalar_one_or_none()
    if lead is None or lead.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail=f"contact {lead_id} not found")

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
    if active_talk is None:
        raise HTTPException(status_code=404, detail="no active talk found")

    # Serialize against run_turn: if a worker turn holds the lead lock we wait
    # until it commits, ensuring no AI message is sent after the flip to 'human'.
    await acquire_lead_lock(db, tenant.id, lead_id)

    # Atomic check-and-set: only updates if handling_mode is currently 'ai'
    result = await db.execute(
        update(Talk)
        .where(
            Talk.id == active_talk.id,
            Talk.tenant_id == tenant.id,
            Talk.handling_mode == "ai",
        )
        .values(handling_mode="human", assigned_operator_id=user.id)
        .returning(Talk.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=409, detail="already human")

    await db.commit()

    # Best-effort realtime publish — failure MUST NOT fail the request.
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            instance_id = await resolve_instance_id(
                db, tenant_id=tenant.id, channel_label=lead.inbound_channel_label
            )
            if instance_id is not None:
                # active_talk is now in 'human' mode; derive the new state.
                active_talk.handling_mode = "human"
                state = derive_state(active_talk)
                await publish_inbox_event(
                    redis,
                    instance_id=instance_id,
                    type="talk.updated",
                    lead_id=lead_id,
                    payload={
                        "lead_id": str(lead_id),
                        "handling_mode": "human",
                        "state": state,
                    },
                )
        except Exception:
            log.exception("console_inbox.takeover_realtime_error", lead_id=str(lead_id))

    return {"talk_id": active_talk.id, "handling_mode": "human"}


# ---------------------------------------------------------------------------
# POST /contacts/{lead_id}/release  -> 200 | 404 | 409
# ---------------------------------------------------------------------------

@router.post("/contacts/{lead_id}/release")
async def release_talk(
    lead_id: uuid.UUID,
    request: Request,
    ctx: TenantCtx,
    db: DbSession,
) -> dict:
    tenant, user = ctx

    lead = (
        await db.execute(select(Lead).where(Lead.id == lead_id))
    ).scalar_one_or_none()
    if lead is None or lead.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail=f"contact {lead_id} not found")

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
    if active_talk is None:
        raise HTTPException(status_code=404, detail="no active talk found")

    # Atomic check-and-set: only updates if handling_mode is currently 'human'
    result = await db.execute(
        update(Talk)
        .where(
            Talk.id == active_talk.id,
            Talk.tenant_id == tenant.id,
            Talk.handling_mode == "human",
        )
        .values(handling_mode="ai", assigned_operator_id=None)
        .returning(Talk.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=409, detail="not human")

    await db.commit()

    # Best-effort realtime publish — failure MUST NOT fail the request.
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            instance_id = await resolve_instance_id(
                db, tenant_id=tenant.id, channel_label=lead.inbound_channel_label
            )
            if instance_id is not None:
                # active_talk is now in 'ai' mode; derive the new state.
                active_talk.handling_mode = "ai"
                state = derive_state(active_talk)
                await publish_inbox_event(
                    redis,
                    instance_id=instance_id,
                    type="talk.updated",
                    lead_id=lead_id,
                    payload={
                        "lead_id": str(lead_id),
                        "handling_mode": "ai",
                        "state": state,
                    },
                )
        except Exception:
            log.exception("console_inbox.release_realtime_error", lead_id=str(lead_id))

    return {"talk_id": active_talk.id, "handling_mode": "ai"}


# ---------------------------------------------------------------------------
# POST /contacts/{lead_id}/send  -> 200 | 404 | 409 | 422
# ---------------------------------------------------------------------------

@router.post("/contacts/{lead_id}/send")
async def send_operator_message(
    lead_id: uuid.UUID,
    request: Request,
    body: SendBody,
    ctx: TenantCtx,
    db: DbSession,
    registry: RegistryDep,
) -> dict:
    """Send a free-text message from the operator to a lead.

    Requires the lead's active talk to be in 'human' handling_mode.
    Idempotent: a duplicate client_message_id returns the existing
    OutboundMessage without re-sending.
    Raises 422 if the WhatsApp 24h window has expired.
    """
    tenant, _user = ctx

    # 1. Load lead (404 if missing or foreign).
    lead = (
        await db.execute(select(Lead).where(Lead.id == lead_id))
    ).scalar_one_or_none()
    if lead is None or lead.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail=f"contact {lead_id} not found")

    # 2. Find the lead's ACTIVE talk (status in active/requires_review, latest).
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
    if active_talk is None:
        raise HTTPException(status_code=404, detail="no active talk found")

    # 3. Require human handling_mode.
    if active_talk.handling_mode != "human":
        raise HTTPException(status_code=409, detail="take over the conversation first")

    # 4. Idempotency: return existing OutboundMessage if client_message_id already seen.
    existing: OutboundMessage | None = (
        await db.execute(
            select(OutboundMessage).where(
                OutboundMessage.tenant_id == tenant.id,
                OutboundMessage.talkflow_id == active_talk.id,
                OutboundMessage.client_message_id == body.client_message_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {
            "outbound_id": existing.id,
            "external_id": existing.external_id,
            "status": existing.status,
        }

    # 5. Resolve adapter and send.
    adapter = registry.get_for_tenant(tenant)
    try:
        result = await adapter.send_text(lead.whatsapp_e164, body.text)
    except WindowExpiredError as exc:
        raise HTTPException(
            status_code=422, detail="24h window closed; template required"
        ) from exc
    except TerminalError as exc:
        raise HTTPException(
            status_code=422, detail=f"send failed: {type(exc).__name__}"
        ) from exc

    # 6. Insert OutboundMessage and commit.
    now = datetime.now(UTC)
    outbound = OutboundMessage(
        tenant_id=tenant.id,
        talkflow_id=active_talk.id,
        lead_id=lead.id,
        provider="whatsapp_cloud",
        message_type="text",
        body_text=body.text,
        status="sent",
        external_id=result.external_id,
        triggered_by="operator",
        client_message_id=body.client_message_id,
        sent_at=now,
    )
    db.add(outbound)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Concurrent POST with same client_message_id already committed first; return it.
        existing_after_conflict: OutboundMessage | None = (
            await db.execute(
                select(OutboundMessage).where(
                    OutboundMessage.tenant_id == tenant.id,
                    OutboundMessage.talkflow_id == active_talk.id,
                    OutboundMessage.client_message_id == body.client_message_id,
                )
            )
        ).scalar_one_or_none()
        if existing_after_conflict is None:
            raise
        return {
            "outbound_id": existing_after_conflict.id,
            "external_id": existing_after_conflict.external_id,
            "status": existing_after_conflict.status,
        }

    # Best-effort realtime publish — failure MUST NOT fail the request.
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await publish_message_created(
                redis,
                db,
                tenant_id=tenant.id,
                lead=lead,
                body_preview=body.text[:120],
            )
        except Exception:
            log.exception("console_inbox.send_realtime_error", lead_id=str(lead_id))

    return {
        "outbound_id": outbound.id,
        "external_id": outbound.external_id,
        "status": outbound.status,
    }
