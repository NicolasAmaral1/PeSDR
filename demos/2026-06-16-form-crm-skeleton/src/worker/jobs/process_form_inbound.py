"""Worker job arq: process_form_inbound.

Enfileirado pelo route handler `forms.py` após persistir submission. Roda
async (não trava webhook).

Roteiro (vide §3.4 da spec):
1. Load submission + tenant + lead
2. Resolve TreeFlow inicial via tenant.yaml > forms.<provider>.start_treeflow
3. Create Talk + TalkFlowState com `collected` pré-populado com field_values
4. Se proactive_first_message.enabled:
   - Render params via Jinja2
   - Resolve messaging_adapter
   - messaging.send_template(...)
   - Audit em outbound_messages com triggered_by='form_inbound'
5. Mark submission processed
6. Talk fica em status=active aguardando lead responder no WhatsApp

Erros tratados:
- PolicyError (HSM não aprovado) → Talk.status='requires_review', operadora vê
- RecipientUnreachable → lead.status='unreachable', talk continua mas inerte
- AuthError (Meta token bad) → alert + worker retry
- Outros → arq retry (tenacity)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.db.session import session_factory
from ai_sdr.flowengine.actions.templating import render_params
from ai_sdr.forms.ingest import create_talk_with_state
from ai_sdr.messaging.errors import (
    AuthError,
    MessagingError,
    PolicyError,
    RecipientUnreachable,
)
from ai_sdr.messaging.factory import build_messaging_adapter
from ai_sdr.models.inbound_form_submission import InboundFormSubmission
from ai_sdr.models.lead import Lead
from ai_sdr.repositories.outbound_audit import record_outbound  # Plano 10
from ai_sdr.secrets.sops_loader import SopsLoader

log = structlog.get_logger(__name__)


async def process_form_inbound(ctx: dict[str, Any], submission_id_str: str) -> None:
    """arq job — processa 1 submission.

    Args:
        ctx: arq context (contains tenant_loader, etc).
        submission_id_str: UUID string da submission row.
    """
    submission_id = UUID(submission_id_str)

    async with session_factory() as session:
        # Worker é trusted — cross-tenant lookup necessário
        await session.execute(text("SET LOCAL row_security = off"))

        sub = await session.get(InboundFormSubmission, submission_id)
        if sub is None:
            log.info("form.submission.not_found", submission_id=submission_id_str)
            return
        if sub.status != "queued":
            log.info(
                "form.submission.already_processed",
                submission_id=submission_id_str,
                status=sub.status,
            )
            return

        # Set tenant context pra queries tenant-scoped abaixo
        await set_tenant_context(session, sub.tenant_id)

        # TODO: implementação completa do happy path + error paths
        # tenant_loader = ctx["tenant_loader"]
        # tenant = await tenant_loader.load_by_id(sub.tenant_id)
        # lead = await session.get(Lead, sub.lead_id)
        #
        # forms_cfg = tenant.forms[sub.provider]
        # treeflow_id = forms_cfg.start_treeflow
        #
        # talk = await create_talk_with_state(
        #     session=session, tenant=tenant, lead=lead,
        #     treeflow_id=treeflow_id, preloaded_collected=sub.field_values,
        # )
        #
        # if forms_cfg.proactive_first_message and forms_cfg.proactive_first_message.enabled:
        #     pfm = forms_cfg.proactive_first_message
        #     secrets = SopsLoader.load(tenant.slug)
        #     messaging = build_messaging_adapter(tenant.messaging, secrets)
        #     params = render_params(pfm.params, {
        #         "collected": sub.field_values,
        #         "lead": {"whatsapp_e164": lead.whatsapp_e164},
        #     })
        #     try:
        #         result = await messaging.send_template(
        #             to=lead.whatsapp_e164,
        #             template_ref=pfm.template_ref,
        #             language=pfm.language,
        #             params=params,
        #         )
        #         await record_outbound(
        #             session, talk.id, result, triggered_by="form_inbound"
        #         )
        #         log.info("form.proactive_sent", lead_id=str(lead.id), talk_id=str(talk.id))
        #     except (PolicyError, AuthError) as exc:
        #         log.error("form.proactive_failed_terminal", err=str(exc), lead_id=str(lead.id))
        #         talk.status = "requires_review"
        #         talk.requires_review_reason = "proactive_hsm_failed"
        #     except RecipientUnreachable:
        #         lead.status = "unreachable"
        #         lead.unreachable_reason = "proactive_hsm_recipient_unreachable"
        #     except MessagingError as exc:
        #         log.warning("form.proactive_failed_transient", err=str(exc), lead_id=str(lead.id))
        #         raise  # arq retry
        #
        # sub.status = "processed"
        # sub.processed_at = datetime.now(timezone.utc)
        # await session.commit()

        raise NotImplementedError("Fase A T8 — process_form_inbound worker")
