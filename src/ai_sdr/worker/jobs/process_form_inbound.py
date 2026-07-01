"""process_form_inbound — bootstrap a Talk from a form submission.

Lifecycle (spec 2026-06-16 §3.4):

  1. Load the queued submission row.
  2. Resolve tenant + lead + tenant config.
  3. Persist form field_values into lead.acquisition_metadata so the
     TreeFlow's system prompt can see them on the first real turn. (MVP
     does not pre-populate TalkFlowState.collected — that's a future
     iteration. The HSM template gets the lead's name directly via
     `proactive_first_message.params` rendered against field_values.)
  4. Flip lead.status from 'pending_assignment' to 'active' so the worker
     drains future inbounds normally.
  5. Create the TalkFlow row pinned to the start_treeflow's latest
     published version.
  6. If proactive_first_message.enabled: render params (sandboxed Jinja2),
     send_template via messaging adapter, audit via record_outbound_sent.
  7. Mark submission status='processed'.

Errors during HSM send (PolicyError, AuthError, RecipientUnreachable):
the Talk is still created — submission goes to status='error' with the
detail. Operator intervenes via console; the lead never gets the first
message but no data is lost.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select, text

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.errors import (
    AuthError,
    MessagingError,
    PolicyError,
    RecipientUnreachable,
)
from ai_sdr.messaging.factory import build_messaging_adapter
from ai_sdr.models.inbound_form_submission import InboundFormSubmission
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.observability.outbound_audit import (
    record_outbound_failed,
    record_outbound_sent,
)
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.loader import TreeFlowLoader
from ai_sdr.treeflow.runtime import TalkFlowRuntime

log = structlog.get_logger(__name__)

_JINJA_ENV = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)


def _render_param_list(
    params: list[str], context: dict[str, Any]
) -> list[str]:
    """Render each Jinja2 expression in ``params`` against ``context``.

    Returns positional list ready for adapter.send_template.
    """
    out: list[str] = []
    for tpl in params:
        try:
            out.append(_JINJA_ENV.from_string(tpl).render(**context))
        except TemplateError as exc:
            raise RuntimeError(
                f"failed to render proactive_first_message param {tpl!r}: {exc}"
            ) from exc
    return out


async def process_form_inbound(
    ctx: dict[str, Any], tenant_id_str: str, submission_id_str: str
) -> None:
    tenant_id = uuid.UUID(tenant_id_str)
    submission_id = uuid.UUID(submission_id_str)
    session_factory = ctx["session_factory"]

    async with session_factory() as db:
        # Bypass RLS for the cross-tenant tenant lookup.
        await db.execute(text("SET LOCAL row_security = off"))

        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        if tenant is None:
            log.warning(
                "form.process.tenant_not_found", tenant_id=tenant_id_str
            )
            return

        await set_tenant_context(db, tenant.id)

        submission = (
            await db.execute(
                select(InboundFormSubmission).where(
                    InboundFormSubmission.id == submission_id,
                    InboundFormSubmission.tenant_id == tenant.id,
                )
            )
        ).scalar_one_or_none()
        if submission is None:
            log.warning(
                "form.process.submission_not_found",
                submission_id=submission_id_str,
            )
            return
        if submission.status != "queued":
            log.info(
                "form.process.submission_not_queued",
                submission_id=submission_id_str,
                status=submission.status,
            )
            return

        if submission.lead_id is None:
            await _mark_error(db, submission, "submission has no lead_id")
            return

        lead = (
            await db.execute(select(Lead).where(Lead.id == submission.lead_id))
        ).scalar_one_or_none()
        if lead is None:
            await _mark_error(db, submission, "lead vanished between webhook and worker")
            return

        tenants_dir = Path(get_settings().tenants_dir)
        tenant_cfg = TenantLoader(tenants_dir).load(tenant.slug)
        secrets = SopsLoader(tenants_dir).load(tenant.slug)

        form_cfg = tenant_cfg.forms.get(submission.provider)
        if form_cfg is None or not form_cfg.enabled:
            await _mark_error(
                db,
                submission,
                f"tenant.yaml: form provider {submission.provider!r} not enabled",
            )
            return

        # Step 3: stash field_values on lead so the agent can see them.
        new_acq = dict(lead.acquisition_metadata or {})
        new_acq.setdefault("form", {})[submission.provider] = dict(
            submission.field_values
        )
        lead.acquisition_metadata = new_acq

        # Step 4: flip to active so future inbounds drain via process_lead_inbox.
        if lead.status == "pending_assignment":
            lead.status = "active"

        # Step 5: create the TalkFlow.
        runtime = TalkFlowRuntime(
            tenant_loader=TenantLoader(tenants_dir),
            treeflow_loader=TreeFlowLoader(tenants_dir),
            sops_loader=SopsLoader(tenants_dir),
        )
        try:
            talkflow = await runtime.create(
                db, tenant, lead.id, form_cfg.start_treeflow
            )
        except ValueError as exc:
            await _mark_error(
                db, submission, f"TalkFlowRuntime.create failed: {exc}"
            )
            return

        # Step 6: proactive HSM (if configured).
        if (
            form_cfg.proactive_first_message
            and form_cfg.proactive_first_message.enabled
        ):
            await _send_proactive_hsm(
                db,
                tenant=tenant,
                tenant_cfg=tenant_cfg,
                secrets=secrets,
                lead=lead,
                talkflow=talkflow,
                submission=submission,
                pfm_cfg=form_cfg.proactive_first_message,
            )

        # Step 7: mark processed (idempotent — re-runs see status!='queued' and skip).
        submission.status = "processed"
        submission.processed_at = datetime.now(UTC)
        await db.commit()

        log.info(
            "form.process.completed",
            submission_id=submission_id_str,
            tenant_slug=tenant.slug,
            talkflow_id=str(talkflow.id),
            lead_id=str(lead.id),
        )


async def _mark_error(
    db: Any, submission: InboundFormSubmission, detail: str
) -> None:
    submission.status = "error"
    submission.error_detail = detail
    submission.processed_at = datetime.now(UTC)
    await db.commit()
    log.warning(
        "form.process.error",
        submission_id=str(submission.id),
        provider=submission.provider,
        detail=detail,
    )


async def _send_proactive_hsm(
    db: Any,
    *,
    tenant: Any,
    tenant_cfg: Any,
    secrets: Any,
    lead: Any,
    talkflow: Any,
    submission: InboundFormSubmission,
    pfm_cfg: Any,
) -> None:
    """Render params, send HSM, audit. Failures don't raise — they degrade
    to status='error' on the submission (kept handled here vs raised up so
    the caller still commits lead/talkflow rows)."""

    if tenant_cfg.messaging is None:
        await _mark_error(db, submission, "tenant.yaml has no messaging block")
        return

    if lead.whatsapp_e164 is None:
        await _mark_error(db, submission, "lead has no whatsapp_e164 for HSM")
        return

    # Render params against form field_values + lead facts.
    context = {
        "collected": dict(submission.field_values),
        "lead": {
            "id": str(lead.id),
            "whatsapp_e164": lead.whatsapp_e164,
            "external_label": lead.external_label,
        },
        "tenant": {
            "slug": tenant.slug,
            "display_name": tenant_cfg.display_name,
        },
    }
    try:
        params = _render_param_list(pfm_cfg.params, context)
    except RuntimeError as exc:
        await _mark_error(db, submission, f"param render failed: {exc}")
        return

    try:
        adapter = build_messaging_adapter(tenant_cfg.messaging, secrets)
    except Exception as exc:
        await _mark_error(
            db, submission, f"messaging adapter build failed: {exc}"
        )
        return

    now = datetime.now(UTC)
    try:
        result = await adapter.send_template(
            to=lead.whatsapp_e164,
            template_ref=pfm_cfg.template_ref,
            language=pfm_cfg.language,
            params=params,
        )
    except (PolicyError, AuthError, RecipientUnreachable) as exc:
        await record_outbound_failed(
            db,
            tenant=tenant,
            talkflow=talkflow,
            lead=lead,
            provider=tenant_cfg.messaging.provider,
            message_type="template",
            triggered_by="form_inbound",
            error_detail=str(exc),
            sent_at=now,
            template_ref=pfm_cfg.template_ref,
            template_language=pfm_cfg.language,
            template_params=params,
        )
        await _mark_error(db, submission, f"HSM send failed: {exc}")
        # mark error already commits; that's fine — Talk + lead.status
        # changes already flushed survive because they were on the same tx.
        if isinstance(exc, RecipientUnreachable):
            lead.status = "unreachable"
            lead.unreachable_reason = str(exc)
            await db.commit()
        return
    except MessagingError as exc:
        log.error(
            "form.process.hsm_unexpected_messaging_error",
            err=str(exc),
            submission_id=str(submission.id),
        )
        await _mark_error(db, submission, f"HSM unexpected error: {exc}")
        return

    await record_outbound_sent(
        db,
        tenant=tenant,
        talkflow=talkflow,
        lead=lead,
        provider=tenant_cfg.messaging.provider,
        message_type="template",
        triggered_by="form_inbound",
        sent_at=now,
        external_id=result.external_id,
        template_ref=pfm_cfg.template_ref,
        template_language=pfm_cfg.language,
        template_params=params,
    )
    log.info(
        "form.process.hsm_sent",
        submission_id=str(submission.id),
        template_ref=pfm_cfg.template_ref,
        external_id=result.external_id,
    )
