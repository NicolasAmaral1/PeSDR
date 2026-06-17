"""POST/GET /webhooks/{tenant_slug}/form/{provider} — entrada de formulário.

URL pattern paralelo ao messaging (`/webhooks/{slug}/{provider}`), com path
segment `form/` deixando explícita a categoria.

Roteiro do handler (vide §3.1 da spec):
1. resolve tenant via slug (404 se não)
2. tenant.yaml > forms.<provider>.enabled = true (404 se não)
3. FormProviderAdapter registrado pra <provider> (404 se não)
4. adapter.handle_submission → raise SignatureError (401) | MalformedPayload (400) | ok
5. find_or_create_lead_by_form
6. INSERT inbound_form_submissions ON CONFLICT DO NOTHING
7. enqueue process_form_inbound
8. retorna 200 com body json

Handler NÃO chama LLM — só persiste e enfileira. <100ms.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import adapter_registry, arq_pool, db_session
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.forms.errors import (
    MalformedPayload,
    SignatureError,
    UnknownFormProviderError,
)
from ai_sdr.forms.ingest import find_or_create_lead_by_form
from ai_sdr.models.inbound_form_submission import InboundFormSubmission
from ai_sdr.tenant_loader.loader import TenantNotFoundError

router = APIRouter()


@router.post("/webhooks/{tenant_slug}/form/{provider}")
async def form_webhook_ingest(
    tenant_slug: str,
    provider: str,
    request: Request,
    session: AsyncSession = Depends(db_session),
    registry: Any = Depends(adapter_registry),
    pool: Any = Depends(arq_pool),
) -> dict[str, str]:
    """Ingere submission de formulário.

    Returns:
        {"status": "queued", "lead_id": "<uuid>", "submission_id": "<uuid>"}
        ou
        {"status": "skipped_dedupe", "lead_id": "<uuid>"}
    """
    # TODO: implementação real
    # 1. Resolve tenant
    # try:
    #     tenant = registry.tenant_loader.load(tenant_slug)
    # except TenantNotFoundError:
    #     raise HTTPException(404, "tenant not found")
    #
    # 2. Provider habilitado?
    # if provider not in (tenant.forms or {}) or not tenant.forms[provider].enabled:
    #     raise HTTPException(404, "form provider not enabled for tenant")
    #
    # 3. Resolve adapter via registry (cache by tenant_id+provider)
    # secrets = registry.sops_loader.load(tenant_slug)
    # try:
    #     adapter = registry.get_form_adapter(tenant, provider, secrets)
    # except UnknownFormProviderError:
    #     raise HTTPException(404, "form provider not registered")
    #
    # 4. Parse payload via adapter
    # raw_body = await request.body()
    # try:
    #     submission = await adapter.handle_submission(
    #         raw_body=raw_body,
    #         headers=request.headers,
    #         query_params=request.query_params,
    #     )
    # except SignatureError:
    #     raise HTTPException(401, "invalid signature")
    # except MalformedPayload as exc:
    #     raise HTTPException(400, str(exc))
    #
    # 5. Tenant context + find/create lead
    # await set_tenant_context(session, tenant.id)
    # lead = await find_or_create_lead_by_form(session, tenant, submission.lead_identifier)
    #
    # 6. INSERT ... ON CONFLICT DO NOTHING + RETURNING id
    # raw_dict = json.loads(raw_body)
    # stmt = (
    #     pg_insert(InboundFormSubmission)
    #     .values(
    #         tenant_id=tenant.id,
    #         provider=provider,
    #         external_id=submission.external_id,
    #         lead_id=lead.id,
    #         raw=raw_dict,
    #         field_values=submission.field_values,
    #         submitted_at=submission.submitted_at_iso,
    #         status="queued",
    #     )
    #     .on_conflict_do_nothing(
    #         index_elements=["tenant_id", "provider", "external_id"]
    #     )
    #     .returning(InboundFormSubmission.id)
    # )
    # result = await session.execute(stmt)
    # new_id = result.scalar_one_or_none()
    # await session.commit()
    #
    # 7. Enqueue (idempotente — dedup já tratado)
    # if new_id is None:
    #     return {"status": "skipped_dedupe", "lead_id": str(lead.id)}
    # await pool.enqueue_job("process_form_inbound", str(new_id))
    # return {
    #     "status": "queued",
    #     "lead_id": str(lead.id),
    #     "submission_id": str(new_id),
    # }
    raise HTTPException(501, "Fase A T7 — webhook handler not implemented yet")


# Opcional: GET handshake (não usado pelo Respondi; futuro Typeform/etc pode usar)
# @router.get("/webhooks/{tenant_slug}/form/{provider}")
# async def form_webhook_challenge(...):
#     ...
