"""Form webhook routes (spec 2026-06-16 §3.1).

URL shape:
  POST /webhooks/{tenant_slug}/form/{provider}                       ← MVP
  POST /webhooks/{tenant_slug}/form/{provider}/{channel_label}       ← multi-channel hedge

The path segment `form/` distinguishes this from messaging webhooks
(`/webhooks/{slug}/{provider}`). FormProviderAdapter validates auth via a
shared secret query param (`?secret=...`) — Respondi has no HMAC support.

Handler stays under 100ms: persist + enqueue, no LLM call here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import arq_pool, db_session
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.forms.errors import (
    IdentityResolutionError,
    MalformedPayload,
    SignatureError,
    UnknownProvider,
)
from ai_sdr.forms.factory import build_form_adapter
from ai_sdr.forms.ingest import ingest_form_submission
from ai_sdr.models.tenant import Tenant
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader

log = structlog.get_logger(__name__)
router = APIRouter()


async def _load_tenant(db: AsyncSession, slug: str) -> Tenant:
    tenant = (
        await db.execute(select(Tenant).where(Tenant.slug == slug))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant {slug!r} not found")
    return tenant


async def _form_ingest_impl(
    tenant_slug: str,
    provider: str,
    channel_label: str,
    request: Request,
    db: AsyncSession,
    pool: Any,
) -> Response:
    tenant = await _load_tenant(db, tenant_slug)

    tenants_dir = Path(get_settings().tenants_dir)
    tenant_cfg = TenantLoader(tenants_dir).load(tenant.slug)

    form_cfg = tenant_cfg.forms.get(provider)
    if form_cfg is None or not form_cfg.enabled:
        log.info(
            "form.webhook.unknown_provider",
            slug=tenant_slug,
            provider=provider,
        )
        raise HTTPException(
            status_code=404, detail=f"form provider {provider!r} not enabled for tenant"
        )

    secrets = SopsLoader(tenants_dir).load(tenant.slug)
    try:
        adapter = build_form_adapter(provider, form_cfg, secrets)
    except (ValueError, KeyError, UnknownProvider) as exc:
        log.error(
            "form.webhook.adapter_build_failed",
            slug=tenant_slug,
            provider=provider,
            err=str(exc),
        )
        raise HTTPException(status_code=500, detail="adapter init failed") from exc

    raw_body = await request.body()
    headers = dict(request.headers)
    url_params = dict(request.query_params)
    try:
        submission = await adapter.handle_submission(raw_body, headers, url_params)
    except SignatureError as exc:
        log.warning("form.webhook.signature_error", slug=tenant_slug, err=str(exc))
        raise HTTPException(status_code=401, detail="invalid signature") from exc
    except MalformedPayload as exc:
        log.warning("form.webhook.malformed_payload", slug=tenant_slug, err=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await set_tenant_context(db, tenant.id)
    try:
        result = await ingest_form_submission(
            db, tenant, provider, submission, inbound_channel_label=channel_label
        )
    except IdentityResolutionError as exc:
        log.warning(
            "form.webhook.identity_resolution_failed",
            slug=tenant_slug,
            external_id=submission.external_id,
            err=str(exc),
        )
        await db.rollback()
        # Still 200 — Respondi shouldn't retry. The submission is dropped
        # (we have no Lead to attach it to). Surface in logs for ops review.
        return Response(status_code=200)

    await db.commit()

    if result.status == "queued":
        await pool.enqueue_job(
            "process_form_inbound",
            str(tenant.id),
            str(result.submission_id),
        )

    log.info(
        "form.webhook.ingested",
        slug=tenant_slug,
        provider=provider,
        channel_label=channel_label,
        external_id=submission.external_id,
        status=result.status,
        submission_id=str(result.submission_id),
        lead_id=str(result.lead_id),
    )
    return Response(status_code=200)


@router.post("/webhooks/{tenant_slug}/form/{provider}")
async def form_ingest_legacy(
    tenant_slug: str,
    provider: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(db_session)],
    pool: Annotated[Any, Depends(arq_pool)],
) -> Response:
    return await _form_ingest_impl(
        tenant_slug, provider, "main", request, db, pool
    )


@router.post("/webhooks/{tenant_slug}/form/{provider}/{channel_label}")
async def form_ingest_with_channel(
    tenant_slug: str,
    provider: str,
    channel_label: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(db_session)],
    pool: Annotated[Any, Depends(arq_pool)],
) -> Response:
    return await _form_ingest_impl(
        tenant_slug, provider, channel_label, request, db, pool
    )
