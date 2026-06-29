"""Persist inbound form submissions + resolve submitter to a Lead.

Mirrors `messaging/ingest.py`. Caller is responsible for setting tenant
context via set_tenant_context() and committing the session.

MVP scope (spec §3.3):
  - Resolution is by `whatsapp_e164` E.164 — the channel the agent will
    message on. If the form didn't capture a phone (or normalization failed),
    raise IdentityResolutionError. The webhook handler still returns 200 (so
    Respondi doesn't keep retrying), and the row is persisted with status='error'.
  - email is captured into `Lead.acquisition_metadata` for BI but is NOT a
    matching key — Plano 6 (Identity Resolver) generalizes when needed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.forms.base import IngestedFormSubmission, LeadIdentifier
from ai_sdr.forms.errors import IdentityResolutionError
from ai_sdr.models.inbound_form_submission import InboundFormSubmission
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant


@dataclass(frozen=True)
class FormIngestResult:
    status: Literal["queued", "skipped_dedupe"]
    submission_id: uuid.UUID
    lead_id: uuid.UUID


async def find_or_create_lead_by_form(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    identifier: LeadIdentifier,
    inbound_channel_label: str = "main",
) -> Lead:
    """Return the lead matching this LeadIdentifier, creating one if needed.

    Matching key (MVP): `whatsapp_e164`. If absent → IdentityResolutionError.
    """
    if not identifier.whatsapp_e164:
        raise IdentityResolutionError(
            "form submission missing whatsapp_e164; cannot send proactive HSM"
        )

    existing = (
        await session.execute(
            select(Lead).where(
                Lead.tenant_id == tenant_id,
                Lead.whatsapp_e164 == identifier.whatsapp_e164,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    acquisition_metadata: dict[str, object] = {}
    if identifier.email:
        acquisition_metadata["email"] = identifier.email
    if identifier.external_label:
        acquisition_metadata["form_external_id"] = identifier.external_label

    lead = Lead(
        tenant_id=tenant_id,
        whatsapp_e164=identifier.whatsapp_e164,
        external_label=identifier.external_label,
        status="pending_assignment",
        inbound_channel_label=inbound_channel_label,
        acquisition_metadata=acquisition_metadata,
    )
    session.add(lead)
    await session.flush()
    return lead


async def ingest_form_submission(
    session: AsyncSession,
    tenant: Tenant,
    provider: str,
    submission: IngestedFormSubmission,
    inbound_channel_label: str = "main",
) -> FormIngestResult:
    """Resolve submitter → lead, then INSERT ... ON CONFLICT DO NOTHING the
    submission row. Returns FormIngestResult so the caller decides whether
    to enqueue the worker job.
    """
    lead = await find_or_create_lead_by_form(
        session, tenant.id, submission.lead_identifier, inbound_channel_label
    )

    submitted_at = datetime.fromisoformat(submission.submitted_at_iso)
    stmt = (
        pg_insert(InboundFormSubmission)
        .values(
            tenant_id=tenant.id,
            provider=provider,
            external_id=submission.external_id,
            lead_id=lead.id,
            raw=dict(submission.raw),
            field_values=dict(submission.field_values),
            submitted_at=submitted_at,
            status="queued",
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "provider", "external_id"]
        )
        .returning(InboundFormSubmission.id)
    )
    result = await session.execute(stmt)
    inserted_id = result.scalar_one_or_none()
    if inserted_id is None:
        # Conflict — fetch existing id for the caller.
        existing_id = (
            await session.execute(
                select(InboundFormSubmission.id).where(
                    InboundFormSubmission.tenant_id == tenant.id,
                    InboundFormSubmission.provider == provider,
                    InboundFormSubmission.external_id == submission.external_id,
                )
            )
        ).scalar_one()
        return FormIngestResult(
            status="skipped_dedupe", submission_id=existing_id, lead_id=lead.id
        )
    return FormIngestResult(
        status="queued", submission_id=inserted_id, lead_id=lead.id
    )
