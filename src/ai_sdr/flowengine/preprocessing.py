"""Preprocessing stage of the FlowEngine pipeline.

Resolves Lead + Talk for an incoming inbound message. Performs opt-out
detection (raises OptOutDetected if matched). Does NOT call the LLM —
this stage runs cheaply before any LLM cost is incurred.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.state import Message
from ai_sdr.flowengine.treeflow_loader import TreeflowDef
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.repositories.lead_repository import LeadRepository
from ai_sdr.repositories.talk_repository import TalkRepository
from ai_sdr.repositories.talkflow_state_repository import TalkFlowStateRepository

logger = logging.getLogger(__name__)


class OptOutDetected(Exception):
    """Raised when the inbound contains an opt-out keyword (case-insensitive)."""


@dataclass
class PipelineContext:
    """Carried through run_turn — Lead, Talk, inbound, and origin flag."""

    lead: Lead
    talk: Talk
    inbound: InboundMessageRow
    is_new_talk: bool


async def resolve_pipeline_context(
    session: AsyncSession,
    *,
    tenant: Tenant,
    inbound: InboundMessageRow,
    treeflow: TreeflowDef,
    treeflow_version: TreeflowVersion,
    opt_out_keywords: list[str],
) -> PipelineContext:
    """Resolve Lead + Talk, detect opt-out, return PipelineContext.

    Lead resolution: find by ('whatsapp', from_address); create if missing.
    Talk resolution: find_active_for_lead; create if missing.

    Note: the corrupt-YAML / unresolvable-snapshot case is handled in the
    worker (`src/ai_sdr/worker/jobs/inbound.py`) BEFORE this function is
    called, because the YAML parse happens at worker setup time. See
    FE-03a spec §11 + §B2 (`treeflow_version_missing` escalation path).
    """
    text = (inbound.text or inbound.transcription or "").strip()
    if text and _match_opt_out(text, opt_out_keywords):
        raise OptOutDetected(f"inbound matched opt-out keyword in {opt_out_keywords!r}")

    leads = LeadRepository(session)
    lead = await leads.find_by_channel_identifier(tenant.id, "whatsapp", inbound.from_address)
    if lead is None:
        lead = Lead(
            tenant_id=tenant.id,
            channel_identifiers={"whatsapp": inbound.from_address},
            whatsapp_e164=inbound.from_address,
            status="active",
        )
        session.add(lead)
        await session.flush()

    talks = TalkRepository(session)
    existing = await talks.find_active_for_lead(tenant.id, lead.id)
    if existing is not None:
        return PipelineContext(lead=lead, talk=existing, inbound=inbound, is_new_talk=False)

    # FE-03b §5.5: re-engagement detection.
    # When a previous Talk closed and the lead is now sending a fresh inbound,
    # log the event so operators can see the timeline. Per spec §16 the new
    # Talk is created fresh (no reopen) — behavior unchanged.
    previously_closed = await talks.find_most_recent_closed(tenant.id, lead.id)
    if previously_closed is not None:
        logger.info(
            "talk.re_engagement_after_close lead=%s previous_talk=%s "
            "previous_status=%s closed_at=%s",
            lead.id,
            previously_closed.id,
            previously_closed.status,
            previously_closed.closed_at,
        )

    talk = await talks.create(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_id=treeflow.id,
        treeflow_version_id=treeflow_version.id,
    )
    await session.flush()

    # Bootstrap the runtime state with the first message.
    states = TalkFlowStateRepository(session)
    state = await states.initialize(
        talk_id=talk.id, tenant_id=tenant.id, entry_node=treeflow.entry_node
    )
    first_msg = Message(
        role="user",
        content=(inbound.text or inbound.transcription or "").strip(),
        source="lead",
        turn_index=1,
        timestamp=inbound.received_at or datetime.now(timezone.utc),
    )
    await states.append_message(state, first_msg, max_window=15)
    await session.flush()

    return PipelineContext(lead=lead, talk=talk, inbound=inbound, is_new_talk=True)


def _match_opt_out(text: str, keywords: list[str]) -> bool:
    """Whole-word, case-insensitive match against any keyword."""
    lowered = text.lower()
    for kw in keywords:
        pattern = rf"\b{re.escape(kw.lower())}\b"
        if re.search(pattern, lowered):
            return True
    return False
