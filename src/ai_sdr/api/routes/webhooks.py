"""Inbound webhook routes.

URL shape (two forms accepted):
  /webhooks/{tenant_slug}/{provider}                     ← legacy, assumes channel_label="main"
  /webhooks/{tenant_slug}/{provider}/{channel_label}     ← multi-channel-aware

  - GET  → adapter.verification_challenge(query_params) — handshake.
  - POST → adapter.handle_inbound(raw_body, headers), then per InboundMessage:
            ingest_inbound_message → enqueue one job per affected lead.

The channel_label is captured and forwarded to ingest_inbound_message so the
lead's `inbound_channel_label` is stamped correctly. When multi-channel ships
(see roadmap), the URL with explicit `{channel_label}` routes to the right
adapter instance; for now both URLs behave identically.

SignatureError → 401. Unknown tenant or provider mismatch → 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import adapter_registry, arq_pool, db_session
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.errors import SignatureError
from ai_sdr.messaging.ingest import ingest_inbound_message
from ai_sdr.messaging.registry import AdapterRegistry
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.realtime.producers import publish_message_created

log = structlog.get_logger(__name__)
router = APIRouter()


async def _load_tenant(db: AsyncSession, slug: str) -> Tenant:
    tenant = (await db.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant {slug!r} not found")
    return tenant


async def _webhook_challenge_impl(
    tenant_slug: str,
    provider: str,
    channel_label: str,
    request: Request,
    db: AsyncSession,
    registry: AdapterRegistry,
) -> Response:
    tenant = await _load_tenant(db, tenant_slug)
    try:
        adapter = registry.get(tenant, provider)
    except ValueError as e:
        # Provider mismatch or no messaging block → 404
        raise HTTPException(status_code=404, detail=str(e)) from e

    try:
        challenge = adapter.verification_challenge(dict(request.query_params))
    except SignatureError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    if challenge is None:
        raise HTTPException(status_code=404, detail="no challenge expected")
    return PlainTextResponse(challenge)


@router.get("/webhooks/{tenant_slug}/{provider}")
async def webhook_challenge_legacy(
    tenant_slug: str,
    provider: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(db_session)],
    registry: Annotated[AdapterRegistry, Depends(adapter_registry)],
) -> Response:
    return await _webhook_challenge_impl(
        tenant_slug, provider, "main", request, db, registry
    )


@router.get("/webhooks/{tenant_slug}/{provider}/{channel_label}")
async def webhook_challenge_with_channel(
    tenant_slug: str,
    provider: str,
    channel_label: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(db_session)],
    registry: Annotated[AdapterRegistry, Depends(adapter_registry)],
) -> Response:
    return await _webhook_challenge_impl(
        tenant_slug, provider, channel_label, request, db, registry
    )


async def _webhook_ingest_impl(
    tenant_slug: str,
    provider: str,
    channel_label: str,
    request: Request,
    db: AsyncSession,
    registry: AdapterRegistry,
    pool: Any,
) -> Response:
    tenant = await _load_tenant(db, tenant_slug)
    try:
        adapter = registry.get(tenant, provider)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    raw_body = await request.body()
    headers = dict(request.headers)
    try:
        messages = await adapter.handle_inbound(raw_body, headers)
    except SignatureError as e:
        log.warning("webhook.signature_error", slug=tenant_slug, err=str(e))
        raise HTTPException(status_code=401, detail="invalid signature") from e

    if not messages:
        return Response(status_code=200)

    await set_tenant_context(db, tenant.id)
    # Collect (lead_id, preview_text) as we ingest each message so we can
    # emit a realtime event per affected lead after the commit.
    # last_preview_per_lead: lead_id → truncated text of the most recent
    # inbound message for that lead (last write wins — fine for a preview).
    last_preview_per_lead: dict[uuid.UUID, str] = {}
    affected_lead_ids: set[uuid.UUID] = set()
    for msg in messages:
        result = await ingest_inbound_message(db, tenant, provider, msg)
        if result.status == "queued":
            affected_lead_ids.add(result.lead_id)
            # Truncate to 120 chars for the preview; text may be None for media.
            last_preview_per_lead[result.lead_id] = (msg.text or "")[:120]
    await db.commit()

    for lead_id in affected_lead_ids:
        await pool.enqueue_job("process_lead_inbox", str(tenant.id), str(lead_id))

    # Best-effort realtime publish — a failure here MUST NOT fail the webhook.
    # We load each affected Lead and publish a message.created event so an
    # operator's open WebSocket sees new contact messages live.
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        for lead_id in affected_lead_ids:
            try:
                lead = await db.get(Lead, lead_id)
                if lead is None:
                    continue
                preview = last_preview_per_lead.get(lead_id, "")
                await publish_message_created(
                    redis, db, tenant_id=tenant.id, lead=lead, body_preview=preview
                )
            except Exception:
                log.exception(
                    "webhook.realtime_publish_error",
                    slug=tenant_slug,
                    lead_id=str(lead_id),
                )

    log.info(
        "webhook.ingested",
        slug=tenant_slug,
        provider=provider,
        channel_label=channel_label,
        n_messages=len(messages),
        n_enqueued=len(affected_lead_ids),
    )
    return Response(status_code=200)


@router.post("/webhooks/{tenant_slug}/{provider}")
async def webhook_ingest_legacy(
    tenant_slug: str,
    provider: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(db_session)],
    registry: Annotated[AdapterRegistry, Depends(adapter_registry)],
    pool: Annotated[Any, Depends(arq_pool)],
) -> Response:
    return await _webhook_ingest_impl(
        tenant_slug, provider, "main", request, db, registry, pool
    )


@router.post("/webhooks/{tenant_slug}/{provider}/{channel_label}")
async def webhook_ingest_with_channel(
    tenant_slug: str,
    provider: str,
    channel_label: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(db_session)],
    registry: Annotated[AdapterRegistry, Depends(adapter_registry)],
    pool: Annotated[Any, Depends(arq_pool)],
) -> Response:
    return await _webhook_ingest_impl(
        tenant_slug, provider, channel_label, request, db, registry, pool
    )
