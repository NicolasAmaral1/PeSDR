"""Inbound webhook routes.

URL shape: /webhooks/{tenant_slug}/{provider}

  - GET  → adapter.verification_challenge(query_params) — handshake.
  - POST → adapter.handle_inbound(raw_body, headers), then per InboundMessage:
            ingest_inbound_message → enqueue one job per affected lead.

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
from ai_sdr.models.tenant import Tenant

log = structlog.get_logger(__name__)
router = APIRouter()


async def _load_tenant(db: AsyncSession, slug: str) -> Tenant:
    tenant = (await db.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant {slug!r} not found")
    return tenant


@router.get("/webhooks/{tenant_slug}/{provider}")
async def webhook_challenge(
    tenant_slug: str,
    provider: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(db_session)],
    registry: Annotated[AdapterRegistry, Depends(adapter_registry)],
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


@router.post("/webhooks/{tenant_slug}/{provider}")
async def webhook_ingest(
    tenant_slug: str,
    provider: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(db_session)],
    registry: Annotated[AdapterRegistry, Depends(adapter_registry)],
    pool: Annotated[Any, Depends(arq_pool)],
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
    affected_lead_ids: set[uuid.UUID] = set()
    for msg in messages:
        result = await ingest_inbound_message(db, tenant, provider, msg)
        if result.status == "queued":
            affected_lead_ids.add(result.lead_id)
    await db.commit()

    for lead_id in affected_lead_ids:
        await pool.enqueue_job("process_lead_inbox", str(tenant.id), str(lead_id))

    log.info(
        "webhook.ingested",
        slug=tenant_slug,
        provider=provider,
        n_messages=len(messages),
        n_enqueued=len(affected_lead_ids),
    )
    return Response(status_code=200)
