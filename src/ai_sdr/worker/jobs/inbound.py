"""process_lead_inbox — drain one lead's queued inbound messages.

Concurrency model: per-lead Postgres advisory lock. Different leads run
in parallel (different lock keys); the same lead processes its queue
serially, in `received_at ASC` order. A second job firing for the same
lead while the first is still processing returns immediately — the
first's loop will pick up new messages on its next iteration via the
in-loop re-scan.

Error taxonomy (per Plano 5 spec §8):
  - RecipientUnreachable    → mark lead.status='unreachable'; loop ends
  - WindowExpiredError      → msg.status='error', detail='window_expired';
                              Plano 9 hook (template HSM); loop ends
  - AuthError / PolicyError → msg.status='error'; log+alert; loop ends
  - MessagingError (other)  → msg.status='error'; log; loop ends
"""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.follow_up.duration import parse_duration
from ai_sdr.follow_up.scheduler import (
    cancel_pending_for_lead,
    schedule_next_followup,
)
from ai_sdr.follow_up.treeflow_loader import load_treeflow_follow_up
from ai_sdr.messaging.errors import (
    AuthError,
    MessagingError,
    PolicyError,
    RecipientUnreachable,
    WindowExpiredError,
)
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.observability.outbound_audit import (
    record_outbound_failed,
    record_outbound_sent,
)

log = structlog.get_logger(__name__)


def _stable_lock_key(tenant_id: str, lead_id: str) -> int:
    """Compress (tenant, lead) into a signed int8 for pg_advisory_lock."""
    h = hashlib.sha256(f"{tenant_id}:{lead_id}".encode()).digest()
    # Use first 8 bytes; mask to fit in PostgreSQL's signed bigint.
    return int.from_bytes(h[:8], "big", signed=False) & 0x7FFFFFFFFFFFFFFF


async def _fetch_next_queued(db: AsyncSession, lead_id: uuid.UUID) -> InboundMessageRow | None:
    return (
        await db.execute(
            select(InboundMessageRow)
            .where(
                InboundMessageRow.lead_id == lead_id,
                InboundMessageRow.status == "queued",
            )
            .order_by(InboundMessageRow.received_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _mark_queued_as_skipped(db: AsyncSession, lead_id: uuid.UUID, reason: str) -> None:
    await db.execute(
        update(InboundMessageRow)
        .where(
            InboundMessageRow.lead_id == lead_id,
            InboundMessageRow.status == "queued",
        )
        .values(status="error", error_detail=f"skipped: {reason}")
    )


async def _run_v2_inbox(
    db: AsyncSession,
    *,
    tenant: Tenant,
    lead: Lead,
    adapter,
    tenant_uuid: uuid.UUID,
) -> None:
    """Drain queued inbound messages through FlowEngine run_turn (FE-01b).

    Resolves tenant config + latest TreeflowVersion + LLM + GuardrailConfig
    once per drain, then loops over queued rows in received_at ASC order.
    """
    from pathlib import Path

    from ai_sdr.flowengine.llm_client import main_llm_for_tenant
    from ai_sdr.flowengine.pipeline import run_turn
    from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2
    from ai_sdr.guardrails.validator import GuardrailConfig
    from ai_sdr.models.treeflow_version import TreeflowVersion
    from ai_sdr.secrets.sops_loader import SopsLoader
    from ai_sdr.settings import get_settings
    from ai_sdr.tenant_loader.loader import TenantLoader

    tdir = Path(get_settings().tenants_dir)
    tenant_cfg = TenantLoader(tdir).load(tenant.slug)
    secrets = SopsLoader(tdir).load(tenant.slug)

    tfv = (
        await db.execute(
            select(TreeflowVersion)
            .where(TreeflowVersion.tenant_id == tenant.id)
            .order_by(TreeflowVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if tfv is None:
        log.error(
            "worker.v2_no_treeflow_version",
            tenant_id=str(tenant.id),
            lead_id=str(lead.id),
        )
        return

    treeflow = load_treeflow_v2(tfv.content_yaml)
    llm = main_llm_for_tenant(tenant_cfg.llm.default, secrets=secrets)

    opt_out_keywords = (
        list(tenant_cfg.conversation.optout_stop_words)
        if tenant_cfg.conversation
        else []
    )
    gcfg = tenant_cfg.guardrails
    guardrail_cfg = GuardrailConfig(
        disallowed_price_pattern=(gcfg.disallowed_price_pattern if gcfg else ""),
        allowed_prices=[str(p) for p in (gcfg.allowed_prices if gcfg else [])],
    )

    while True:
        msg = await _fetch_next_queued(db, lead.id)
        if msg is None:
            break
        try:
            result = await run_turn(
                db,
                tenant=tenant,
                treeflow=treeflow,
                treeflow_version=tfv,
                inbound=msg,
                llm=llm,
                adapter=adapter,
                opt_out_keywords=opt_out_keywords,
                guardrail_cfg=guardrail_cfg,
            )
        except RecipientUnreachable as e:
            lead.status = "unreachable"
            lead.unreachable_reason = f"unreachable: {e}"
            msg.status = "error"
            msg.error_detail = f"unreachable: {e}"
            log.warning("worker.v2.recipient_unreachable", lead_id=str(lead.id), err=str(e))
            await db.commit()
            return
        except (AuthError, PolicyError, WindowExpiredError, MessagingError) as e:
            msg.status = "error"
            msg.error_detail = f"{type(e).__name__}: {e}"
            log.error(
                "worker.v2.messaging_error",
                lead_id=str(lead.id),
                err_type=type(e).__name__,
                err=str(e),
            )
            await db.commit()
            return

        if result.outcome == "sent":
            msg.status = "processed"
            msg.processed_at = datetime.now(UTC)
        elif result.outcome in ("opt_out", "lead_banned", "escalated"):
            msg.status = "processed"
            msg.processed_at = datetime.now(UTC)
            msg.error_detail = f"v2_outcome: {result.outcome}"
        else:
            msg.status = "error"
            msg.error_detail = f"v2_outcome: {result.outcome}"

        await db.commit()
        # Tenant context is transaction-local; re-set so the next fetch sees RLS rows.
        await set_tenant_context(db, tenant_uuid)


async def process_lead_inbox(ctx: dict[str, Any], tenant_id: str, lead_id: str) -> None:
    session_factory = ctx["session_factory"]
    registry = ctx["adapter_registry"]
    runtime = ctx.get("runtime")
    if runtime is None:
        # Production: instantiate lazily. Tests inject a stub via ctx.
        from pathlib import Path

        from ai_sdr.secrets.sops_loader import SopsLoader
        from ai_sdr.settings import get_settings
        from ai_sdr.tenant_loader.loader import TenantLoader
        from ai_sdr.treeflow.loader import TreeFlowLoader
        from ai_sdr.treeflow.runtime import TalkFlowRuntime

        tdir = Path(get_settings().tenants_dir)
        runtime = TalkFlowRuntime(
            tenant_loader=TenantLoader(tdir),
            treeflow_loader=TreeFlowLoader(tdir),
            sops_loader=SopsLoader(tdir),
        )

    tenant_uuid = uuid.UUID(tenant_id)
    lead_uuid = uuid.UUID(lead_id)
    lock_key = _stable_lock_key(tenant_id, lead_id)

    async with session_factory() as db:
        await set_tenant_context(db, tenant_uuid)

        got = (await db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key})).scalar()
        if not got:
            log.info(
                "worker.lock_contention",
                tenant_id=tenant_id,
                lead_id=lead_id,
            )
            return

        try:
            tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_uuid))).scalar_one()
            lead = (await db.execute(select(Lead).where(Lead.id == lead_uuid))).scalar_one()

            if lead.status == "pending_assignment":
                return  # operator hasn't assigned

            if lead.status == "unreachable":
                await _mark_queued_as_skipped(db, lead.id, reason="lead_unreachable")
                await db.commit()
                return

            # status == 'active' — find the talkflow
            talkflow = (
                await db.execute(select(TalkFlow).where(TalkFlow.lead_id == lead.id))
            ).scalar_one_or_none()
            if talkflow is None:
                log.error(
                    "worker.active_lead_without_talkflow",
                    tenant_id=tenant_id,
                    lead_id=lead_id,
                )
                return

            adapter = registry.get_for_tenant(tenant)

            # FE-01b feature flag: route to FlowEngine v2 when architecture_version == 2.
            if tenant.architecture_version == 2:
                await _run_v2_inbox(
                    db, tenant=tenant, lead=lead, adapter=adapter, tenant_uuid=tenant_uuid,
                )
                return
            # else: fall through to existing v1 path unchanged

            # P9: lead responded — cancel pending follow-ups, reset counter,
            # reactivate cold talkflow.
            cancelled = await cancel_pending_for_lead(db, lead.id, reason="lead responded")
            if cancelled:
                log.info("follow_up.cancelled_on_inbound", lead_id=str(lead.id), n=cancelled)
            talkflow.follow_up_attempt_number = 0
            if talkflow.status == "cold":
                talkflow.status = "active"
                log.info("follow_up.cold_reactivated", talkflow_id=str(talkflow.id))

            while True:
                msg = await _fetch_next_queued(db, lead.id)
                if msg is None:
                    break

                step_result = await runtime.step(db, tenant, talkflow.id, user_input=msg.text)
                reply_text = step_result.response_text

                try:
                    send_result = await adapter.send_text(to=msg.from_address, text=reply_text)
                    msg.status = "processed"
                    msg.processed_at = datetime.now(UTC)
                    log.info(
                        "worker.msg.processed",
                        msg_id=str(msg.id),
                        sent_external_id=send_result.external_id,
                    )
                    # P10: audit outbound (success). provider comes from the
                    # inbound row (set by the messaging adapter when the
                    # webhook landed); the webhook validates it against
                    # tenant_cfg.messaging.provider at ingestion, so it
                    # matches the adapter just resolved via get_for_tenant().
                    await record_outbound_sent(
                        db,
                        tenant=tenant,
                        talkflow=talkflow,
                        lead=lead,
                        provider=msg.provider,
                        message_type="text",
                        triggered_by="inbound",
                        body_text=reply_text,
                        external_id=send_result.external_id,
                        sent_at=datetime.fromisoformat(send_result.sent_at_iso),
                        inbound_message_id=msg.id,
                    )
                    # P9: agent just spoke — update timestamps + schedule next follow-up.
                    talkflow.last_agent_message_at = datetime.now(UTC)
                    talkflow.last_lead_message_at = msg.received_at
                    tf_config = await load_treeflow_follow_up(db, talkflow)
                    if tf_config and tf_config.enabled and tf_config.sequence:
                        await schedule_next_followup(
                            db,
                            talkflow,
                            lead,
                            tenant,
                            tf_config,
                            next_attempt_number=1,
                        )
                        log.info(
                            "follow_up.first_scheduled",
                            lead_id=str(lead.id),
                            at=(
                                datetime.now(UTC) + parse_duration(tf_config.sequence[0].after)
                            ).isoformat(),
                        )
                except RecipientUnreachable as e:
                    lead.status = "unreachable"
                    lead.unreachable_reason = f"unreachable: {e}"
                    msg.status = "error"
                    msg.error_detail = f"unreachable: {e}"
                    log.warning(
                        "worker.recipient_unreachable",
                        lead_id=lead_id,
                        err=str(e),
                    )
                    await record_outbound_failed(
                        db,
                        tenant=tenant,
                        talkflow=talkflow,
                        lead=lead,
                        provider=msg.provider,
                        message_type="text",
                        triggered_by="inbound",
                        body_text=reply_text,
                        error_detail=f"{type(e).__name__}: {e}",
                        sent_at=datetime.now(UTC),
                        inbound_message_id=msg.id,
                    )
                    await db.commit()
                    return
                except WindowExpiredError as e:
                    # P9: try the tenant's reengagement_template fallback.
                    from pathlib import Path

                    from ai_sdr.follow_up.jinja import render_params
                    from ai_sdr.settings import get_settings
                    from ai_sdr.tenant_loader.loader import TenantLoader

                    tenant_cfg = TenantLoader(Path(get_settings().tenants_dir)).load(tenant.slug)
                    # If we reached WindowExpiredError, an adapter was built from
                    # tenant_cfg.messaging earlier in this turn — so messaging
                    # must be set. Narrow for mypy.
                    assert tenant_cfg.messaging is not None
                    messaging_cfg = tenant_cfg.messaging
                    # P10: audit the failed text send first
                    await record_outbound_failed(
                        db,
                        tenant=tenant,
                        talkflow=talkflow,
                        lead=lead,
                        provider=messaging_cfg.provider,
                        message_type="text",
                        triggered_by="inbound",
                        body_text=reply_text,
                        error_detail=f"WindowExpiredError: {e}",
                        sent_at=datetime.now(UTC),
                        inbound_message_id=msg.id,
                    )
                    reeng = messaging_cfg.reengagement_template
                    if reeng is not None:
                        try:
                            params = render_params(
                                reeng.params,
                                lead=lead,
                                tenant=tenant,
                                collected={},
                            )
                            template_result = await adapter.send_template(
                                to=msg.from_address,
                                template_ref=reeng.template_ref,
                                language=reeng.language,
                                params=params,
                            )
                            msg.status = "processed"
                            msg.processed_at = datetime.now(UTC)
                            msg.error_detail = "window_expired; recovered via reengagement template"
                            talkflow.last_agent_message_at = datetime.now(UTC)
                            log.info(
                                "messaging.window_expired_recovered",
                                lead_id=str(lead.id),
                            )
                            # P10: audit the successful template send
                            await record_outbound_sent(
                                db,
                                tenant=tenant,
                                talkflow=talkflow,
                                lead=lead,
                                provider=messaging_cfg.provider,
                                message_type="template",
                                triggered_by="window_expired_recovery",
                                template_ref=reeng.template_ref,
                                template_language=reeng.language,
                                template_params=params,
                                external_id=template_result.external_id,
                                sent_at=datetime.fromisoformat(template_result.sent_at_iso),
                                inbound_message_id=msg.id,
                            )
                        except Exception as e2:
                            msg.status = "error"
                            msg.error_detail = f"window_expired; reengagement failed: {e2}"
                            log.warning(
                                "messaging.reengagement_failed",
                                lead_id=str(lead.id),
                                err=str(e2),
                            )
                            # P10: audit the failed template send
                            await record_outbound_failed(
                                db,
                                tenant=tenant,
                                talkflow=talkflow,
                                lead=lead,
                                provider=messaging_cfg.provider,
                                message_type="template",
                                triggered_by="window_expired_recovery",
                                template_ref=reeng.template_ref,
                                template_language=reeng.language,
                                template_params=params,
                                error_detail=f"reengagement_failed: {e2}",
                                sent_at=datetime.now(UTC),
                                inbound_message_id=msg.id,
                            )
                    else:
                        msg.status = "error"
                        msg.error_detail = f"window_expired: {e}"
                        log.warning(
                            "messaging.window_expired_no_template",
                            lead_id=str(lead.id),
                        )
                        # No second audit row — the original text failure
                        # already covers the window_expired_no_template case.
                    await db.commit()
                    return
                except (AuthError, PolicyError) as e:
                    msg.status = "error"
                    msg.error_detail = f"{type(e).__name__}: {e}"
                    log.error(
                        "worker.terminal_error",
                        lead_id=lead_id,
                        err_type=type(e).__name__,
                        err=str(e),
                    )
                    await record_outbound_failed(
                        db,
                        tenant=tenant,
                        talkflow=talkflow,
                        lead=lead,
                        provider=msg.provider,
                        message_type="text",
                        triggered_by="inbound",
                        body_text=reply_text,
                        error_detail=f"{type(e).__name__}: {e}",
                        sent_at=datetime.now(UTC),
                        inbound_message_id=msg.id,
                    )
                    await db.commit()
                    return
                except MessagingError as e:
                    msg.status = "error"
                    msg.error_detail = f"{type(e).__name__}: {e}"
                    log.error(
                        "worker.messaging_error",
                        lead_id=lead_id,
                        err_type=type(e).__name__,
                        err=str(e),
                    )
                    await record_outbound_failed(
                        db,
                        tenant=tenant,
                        talkflow=talkflow,
                        lead=lead,
                        provider=msg.provider,
                        message_type="text",
                        triggered_by="inbound",
                        body_text=reply_text,
                        error_detail=f"{type(e).__name__}: {e}",
                        sent_at=datetime.now(UTC),
                        inbound_message_id=msg.id,
                    )
                    await db.commit()
                    return

                await db.commit()
                # Tenant context is transaction-local (set_config(..., true));
                # re-set so the next _fetch_next_queued can read RLS rows.
                await set_tenant_context(db, tenant_uuid)
        finally:
            # If the body left the transaction in an aborted state, roll back
            # first so pg_advisory_unlock can run. The advisory lock is
            # session-scoped (not transaction-scoped) so rollback doesn't
            # release it.
            # Best-effort rollback so pg_advisory_unlock can run even if the
            # body left the transaction aborted. If the session itself is dead,
            # there is no rollback path to log against — silent suppress is OK.
            with contextlib.suppress(Exception):
                await db.rollback()
            await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
            await db.commit()
