"""follow_up_scanner — arq cron job, runs every 60s.

Picks all due `pending` follow_up_jobs across tenants and dispatches each
via `_fire_follow_up`. Per-job uses the same per-lead `pg_advisory_lock`
that `process_lead_inbox` uses (serializes scanner against the inbound
worker).
"""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select, text, update

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.follow_up.jinja import render_params
from ai_sdr.follow_up.scheduler import mark_cold_if_exhausted, schedule_next_followup
from ai_sdr.follow_up.treeflow_loader import load_treeflow_follow_up
from ai_sdr.messaging.errors import (
    AuthError,
    MessagingError,
    PolicyError,
    RecipientUnreachable,
)
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.observability.outbound_audit import (
    record_outbound_failed,
    record_outbound_sent,
)

log = structlog.get_logger(__name__)

_BATCH_SIZE = 200


def _stable_lock_key(tenant_id: str, lead_id: str) -> int:
    """Same hash function as process_lead_inbox — ensures the two paths
    serialize via the same Postgres advisory lock."""
    h = hashlib.sha256(f"{tenant_id}:{lead_id}".encode()).digest()
    return int.from_bytes(h[:8], "big", signed=False) & 0x7FFFFFFFFFFFFFFF


async def follow_up_scanner(ctx: dict[str, Any]) -> None:
    """arq.cron entrypoint. Runs every 60s."""
    session_factory = ctx["session_factory"]
    registry = ctx["adapter_registry"]

    async with session_factory() as db:
        # Cross-tenant scan — bypass RLS for this read only.
        await db.execute(text("SET LOCAL row_security = off"))
        rows = (
            await db.execute(
                # PR #24: filtro de cinto — sandbox NÃO cria follow_up_job na fonte,
                # mas filtramos por garantia se algum vazar.
                select(FollowUpJob.id, FollowUpJob.tenant_id, FollowUpJob.lead_id)
                .join(Lead, Lead.id == FollowUpJob.lead_id)
                .where(
                    FollowUpJob.status == "pending",
                    FollowUpJob.scheduled_at <= func.now(),
                    Lead.is_sandbox.is_(False),
                )
                .order_by(FollowUpJob.scheduled_at.asc())
                .limit(_BATCH_SIZE)
            )
        ).all()

    log.info("follow_up.scanner.batch", count=len(rows))
    for row in rows:
        try:
            await _fire_follow_up(
                session_factory,
                registry,
                row.id,
                row.tenant_id,
                row.lead_id,
            )
        except Exception:
            log.exception("follow_up.scanner.job_failed", job_id=str(row.id))


async def _fire_follow_up(
    session_factory: Any,
    registry: Any,
    job_id: uuid.UUID,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """Single-job dispatch. Per-lead advisory lock, race-belt, error
    classification per spec §6.3."""
    lock_key = _stable_lock_key(str(tenant_id), str(lead_id))

    async with session_factory() as db:
        await set_tenant_context(db, tenant_id)

        got = (await db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key})).scalar()
        if not got:
            log.info("follow_up.lock_contention", lead_id=str(lead_id))
            return

        try:
            job = await db.get(FollowUpJob, job_id)
            if job is None or job.status != "pending":
                return

            talkflow = await db.get(TalkFlow, job.talkflow_id)
            lead = await db.get(Lead, job.lead_id)
            tenant = await db.get(Tenant, job.tenant_id)
            if talkflow is None or lead is None or tenant is None:
                job.status = "cancelled"
                job.error_detail = "missing parent row"
                await db.commit()
                return

            # Race-belt
            if talkflow.last_lead_message_at and talkflow.last_lead_message_at > job.scheduled_at:
                job.status = "cancelled"
                job.error_detail = "lead responded after scheduling"
                await db.commit()
                return
            if talkflow.status in ("cold", "completed"):
                job.status = "cancelled"
                job.error_detail = f"talkflow {talkflow.status}"
                await db.commit()
                return

            tf_config = await load_treeflow_follow_up(db, talkflow)
            if tf_config is None or not tf_config.enabled:
                job.status = "cancelled"
                job.error_detail = "treeflow follow_up disabled"
                await db.commit()
                return

            try:
                step = tf_config.sequence[job.attempt_number - 1]
            except IndexError:
                job.status = "error"
                job.error_detail = (
                    f"attempt_number {job.attempt_number} > sequence length "
                    f"{len(tf_config.sequence)}"
                )
                await db.commit()
                return

            params = render_params(
                step.params,
                lead=lead,
                tenant=tenant,
                collected={},
            )
            adapter = registry.get_for_tenant(tenant)

            try:
                result = await adapter.send_template(
                    to=lead.whatsapp_e164,
                    template_ref=step.template_ref,
                    language=step.language,
                    params=params,
                )
            except RecipientUnreachable as e:
                lead.status = "unreachable"
                lead.unreachable_reason = str(e)
                await db.execute(
                    update(FollowUpJob)
                    .where(
                        FollowUpJob.lead_id == lead.id,
                        FollowUpJob.status == "pending",
                    )
                    .values(status="cancelled", error_detail="lead unreachable")
                )
                job.status = "error"
                job.error_detail = f"unreachable: {e}"
                log.warning("follow_up.recipient_unreachable", lead_id=str(lead.id))
                await record_outbound_failed(
                    db,
                    tenant=tenant,
                    talkflow=talkflow,
                    lead=lead,
                    provider="whatsapp_cloud",
                    message_type="template",
                    triggered_by="follow_up_scanner",
                    template_ref=step.template_ref,
                    template_language=step.language,
                    template_params=params,
                    error_detail=f"{type(e).__name__}: {e}",
                    sent_at=datetime.now(UTC),
                    follow_up_job_id=job.id,
                )
                await db.commit()
                return
            except (AuthError, PolicyError, MessagingError) as e:
                job.status = "error"
                job.error_detail = f"{type(e).__name__}: {e}"
                log.error(
                    "follow_up.send_failed",
                    lead_id=str(lead.id),
                    err_type=type(e).__name__,
                    err=str(e),
                )
                await record_outbound_failed(
                    db,
                    tenant=tenant,
                    talkflow=talkflow,
                    lead=lead,
                    provider="whatsapp_cloud",
                    message_type="template",
                    triggered_by="follow_up_scanner",
                    template_ref=step.template_ref,
                    template_language=step.language,
                    template_params=params,
                    error_detail=f"{type(e).__name__}: {e}",
                    sent_at=datetime.now(UTC),
                    follow_up_job_id=job.id,
                )
                await db.commit()
                return

            # Success
            # P10: audit the successful template send. provider hardcoded to
            # "whatsapp_cloud" — templates only work through that adapter in v1;
            # Vialum adapter (future) will refactor to read tenant_cfg.
            await record_outbound_sent(
                db,
                tenant=tenant,
                talkflow=talkflow,
                lead=lead,
                provider="whatsapp_cloud",
                message_type="template",
                triggered_by="follow_up_scanner",
                template_ref=step.template_ref,
                template_language=step.language,
                template_params=params,
                external_id=result.external_id,
                sent_at=datetime.fromisoformat(result.sent_at_iso),
                follow_up_job_id=job.id,
            )
            job.status = "completed"
            job.fired_at = datetime.now(UTC)
            job.sent_external_id = result.external_id
            talkflow.last_agent_message_at = datetime.now(UTC)
            talkflow.follow_up_attempt_number = job.attempt_number

            became_cold = mark_cold_if_exhausted(talkflow, tf_config, job.attempt_number)
            if became_cold:
                log.info(
                    "follow_up.exhausted_marked_cold",
                    talkflow_id=str(talkflow.id),
                    attempts=job.attempt_number,
                )
            else:
                await schedule_next_followup(
                    db,
                    talkflow,
                    lead,
                    tenant,
                    tf_config,
                    next_attempt_number=job.attempt_number + 1,
                )

            await db.commit()
        finally:
            with contextlib.suppress(Exception):
                await db.rollback()
            await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
            await db.commit()
