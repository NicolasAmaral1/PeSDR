"""InboxRepository — lead-anchored contact list + message stream.

The contact list is Lead-anchored: every lead that matches the channel filter
appears, even if it has no active Talk.  Unread counts are per-operator via
OperatorReadMarker.  last_message_at / last_message_preview are derived from
correlated subqueries over inbound_messages + outbound_messages.

Task 4 delivers:
  - derive_state(active_talk) -> str  (pure helper)
  - ContactRow / MessageRow dataclasses
  - list_contacts(session, *, ...) -> list[ContactRow]
  - list_messages(session, *, lead_id, ...) -> list[MessageRow]

Task 6 adds:
  - status / funnel / q filters wired in list_contacts
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, exists, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.operator_read_marker import OperatorReadMarker
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState

# Statuses that constitute an "active" Talk (mirrors TalkRepository).
# "paused" is excluded: derive_state has no branch for it (would render "closed"),
# and it is a v1-unused reserved status.
_ACTIVE_STATUSES = ("active", "requires_review")


# ---------------------------------------------------------------------------
# State derivation (pure)
# ---------------------------------------------------------------------------

def derive_state(active_talk: Any) -> str:
    """Map an active Talk (or None) to a contact-state string.

    Logic:
      - None                         → "awaiting"
      - requires_review              → "requires_review"
      - active + human handling      → "human"
      - active + ai handling         → "ai"
      - anything else                → "closed"
    """
    if active_talk is None:
        return "awaiting"
    if active_talk.status == "requires_review":
        return "requires_review"
    if active_talk.handling_mode == "human":
        return "human"
    if active_talk.status == "active":
        return "ai"
    return "closed"


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class ContactRow:
    lead_id: uuid.UUID
    tenant_id: uuid.UUID
    whatsapp_e164: str | None
    display_name: str | None
    status: str  # Lead.status
    inbound_channel_label: str
    created_at: datetime
    # Derived
    state: str  # derive_state result
    last_message_at: datetime | None
    last_message_preview: str | None
    active_talk: Talk | None
    funnel_node: str | None
    unread: int


@dataclass
class MessageRow:
    id: uuid.UUID
    direction: str  # "inbound" | "outbound"
    text: str | None
    media_type: str
    audio_url: str | None
    created_at: datetime  # received_at or sent_at
    triggered_by: str | None  # "operator" | "ai" | None (inbound always None)


# ---------------------------------------------------------------------------
# list_contacts
# ---------------------------------------------------------------------------

async def list_contacts(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel_label: str,
    user_id: uuid.UUID,
    status: str | None = None,
    funnel: str | None = None,
    q: str | None = None,
    limit: int = 50,
    before: datetime | None = None,
) -> list[ContactRow]:
    """Return a lead-anchored contact list ordered by last_message_at DESC.

    Each row carries the lead's active Talk (if any), funnel_node, unread
    count, and the latest message preview.  Leads with no messages sort last
    (NULLS LAST).

    Filters (Task 6):
      status: one of awaiting|ai|requires_review|human|closed
      funnel: active talk's treeflow_id must match
      q:      ILIKE match on display_name or whatsapp_e164
    """

    # ------------------------------------------------------------------
    # Correlated subquery: max inbound received_at per lead
    # ------------------------------------------------------------------
    inbound_max_sq = (
        select(func.max(InboundMessageRow.received_at))
        .where(
            InboundMessageRow.lead_id == Lead.id,
            InboundMessageRow.tenant_id == tenant_id,
        )
        .correlate(Lead)
        .scalar_subquery()
    )

    # ------------------------------------------------------------------
    # Correlated subquery: max outbound sent_at per lead
    # ------------------------------------------------------------------
    outbound_max_sq = (
        select(func.max(OutboundMessage.sent_at))
        .where(
            OutboundMessage.lead_id == Lead.id,
            OutboundMessage.tenant_id == tenant_id,
        )
        .correlate(Lead)
        .scalar_subquery()
    )

    # last_message_at = GREATEST(inbound_max, outbound_max) — NULL-safe via
    # func.greatest (PostgreSQL GREATEST ignores NULLs).
    # NOTE: label is "msg_last_at" (not "last_message_at") to avoid ambiguity
    # with talks.last_message_at in the active_talk subquery join.
    last_message_at_expr = func.greatest(inbound_max_sq, outbound_max_sq).label(
        "msg_last_at"
    )

    # ------------------------------------------------------------------
    # Correlated subquery: preview text of the latest inbound message
    # ------------------------------------------------------------------
    # We need the text from the row with the highest received_at.
    latest_inbound_text_sq = (
        select(InboundMessageRow.text)
        .where(
            InboundMessageRow.lead_id == Lead.id,
            InboundMessageRow.tenant_id == tenant_id,
        )
        .order_by(InboundMessageRow.received_at.desc())
        .limit(1)
        .correlate(Lead)
        .scalar_subquery()
    )

    # ------------------------------------------------------------------
    # Correlated subquery: preview text of the latest outbound message
    # ------------------------------------------------------------------
    latest_outbound_text_sq = (
        select(OutboundMessage.body_text)
        .where(
            OutboundMessage.lead_id == Lead.id,
            OutboundMessage.tenant_id == tenant_id,
        )
        .order_by(OutboundMessage.sent_at.desc())
        .limit(1)
        .correlate(Lead)
        .scalar_subquery()
    )

    # ------------------------------------------------------------------
    # Correlated subquery: unread inbound count
    # LEFT JOIN operator_read_markers on (user_id, lead_id).
    # null marker → count ALL inbound for this lead.
    # Epoch sentinel: no marker ↔ all messages are unread.
    # ------------------------------------------------------------------
    _EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

    marker_sq = (
        select(OperatorReadMarker.last_read_message_at)
        .where(
            OperatorReadMarker.user_id == user_id,
            OperatorReadMarker.lead_id == Lead.id,
            OperatorReadMarker.tenant_id == tenant_id,
        )
        .correlate(Lead)
        .scalar_subquery()
    )
    unread_sq = (
        select(func.count())
        .where(
            InboundMessageRow.lead_id == Lead.id,
            InboundMessageRow.tenant_id == tenant_id,
            InboundMessageRow.received_at
            > func.coalesce(marker_sq, literal(_EPOCH)),
        )
        .correlate(Lead)
        .scalar_subquery()
    )

    # ------------------------------------------------------------------
    # Active talk lateral join (LEFT OUTER, one per lead)
    # ------------------------------------------------------------------
    active_talk_sq = (
        select(Talk)
        .where(
            Talk.lead_id == Lead.id,
            Talk.tenant_id == tenant_id,
            Talk.status.in_(_ACTIVE_STATUSES),
        )
        .order_by(Talk.created_at.desc())
        .limit(1)
        .correlate(Lead)
    ).subquery("active_talk")

    # ------------------------------------------------------------------
    # funnel_node: current_node from talkflow_states, or treeflow_id fallback
    # ------------------------------------------------------------------
    funnel_node_sq = (
        select(
            func.coalesce(
                select(TalkFlowState.current_node)
                .where(TalkFlowState.talk_id == active_talk_sq.c.id)
                .correlate(active_talk_sq)
                .scalar_subquery(),
                active_talk_sq.c.treeflow_id,
            )
        )
        .correlate(active_talk_sq)
        .scalar_subquery()
    )

    # ------------------------------------------------------------------
    # Base query: Lead LEFT JOIN active_talk
    # ------------------------------------------------------------------
    stmt = (
        select(
            Lead,
            active_talk_sq,
            last_message_at_expr,
            latest_inbound_text_sq.label("latest_inbound_text"),
            latest_outbound_text_sq.label("latest_outbound_text"),
            inbound_max_sq.label("inbound_max"),
            outbound_max_sq.label("outbound_max"),
            unread_sq.label("unread"),
            funnel_node_sq.label("funnel_node"),
        )
        .outerjoin(active_talk_sq, active_talk_sq.c.lead_id == Lead.id)
        .where(
            Lead.tenant_id == tenant_id,
            Lead.inbound_channel_label == channel_label,
        )
    )

    if before is not None:
        stmt = stmt.where(
            func.greatest(inbound_max_sq, outbound_max_sq) < before
        )

    # ------------------------------------------------------------------
    # Task 6 filters
    # ------------------------------------------------------------------

    # status filter: translate the derive_state semantics into SQL predicates
    # on the active_talk subquery columns (joined via LEFT OUTER JOIN above).
    if status is not None:
        _CLOSED_STATUSES = (
            "closed_completed",
            "closed_inactivity",
            "closed_optout",
            "closed_banned",
        )
        if status == "awaiting":
            # No active talk
            stmt = stmt.where(active_talk_sq.c.id.is_(None))
        elif status == "ai":
            # Active talk, status active, handling_mode ai
            stmt = stmt.where(
                active_talk_sq.c.id.is_not(None),
                active_talk_sq.c.status == "active",
                active_talk_sq.c.handling_mode == "ai",
            )
        elif status == "requires_review":
            # Active talk with status requires_review
            stmt = stmt.where(
                active_talk_sq.c.id.is_not(None),
                active_talk_sq.c.status == "requires_review",
            )
        elif status == "human":
            # Active talk, handling_mode human
            stmt = stmt.where(
                active_talk_sq.c.id.is_not(None),
                active_talk_sq.c.handling_mode == "human",
            )
        elif status == "closed":
            # No active talk but ≥1 closed talk for this lead
            closed_exists_sq = (
                select(Talk.id)
                .where(
                    Talk.lead_id == Lead.id,
                    Talk.tenant_id == tenant_id,
                    Talk.status.in_(_CLOSED_STATUSES),
                )
                .correlate(Lead)
                .exists()
            )
            stmt = stmt.where(
                active_talk_sq.c.id.is_(None),
                closed_exists_sq,
            )

    # funnel filter: active talk's treeflow_id must match
    if funnel is not None:
        stmt = stmt.where(active_talk_sq.c.treeflow_id == funnel)

    # q filter: display_name or whatsapp_e164 ILIKE %q%
    if q is not None:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Lead.display_name.ilike(pattern),
                Lead.whatsapp_e164.ilike(pattern),
            )
        )

    stmt = stmt.order_by(last_message_at_expr.desc().nullslast()).limit(limit)

    rows = (await session.execute(stmt)).all()

    results: list[ContactRow] = []
    for row in rows:
        lead: Lead = row[0]

        # Reconstruct active Talk from joined columns (may be all None)
        talk: Talk | None = None
        # The active_talk_sq columns are appended after lead; SQLAlchemy
        # returns them as a Row. We detect whether talk exists by checking id.
        talk_id = row[1]  # subquery columns start at index 1

        # Because we joined subquery columns (not ORM entity), row[1] is the
        # talk.id value (or None).  We need the Talk object.  Load it if present.
        # However, the subquery returns raw column values, not ORM objects.
        # We'll fetch Talk lazily by id when needed.
        if talk_id is not None:
            # The row contains all talk columns from the subquery.
            # Build a Talk-like object from the subquery columns.
            # Column ordering from active_talk_sq follows Talk.__table__.columns.
            # For simplicity and correctness, we load the Talk ORM object by id.
            talk = await session.get(Talk, talk_id)

        last_msg_at: datetime | None = row.msg_last_at
        inbound_max: datetime | None = row.inbound_max
        outbound_max: datetime | None = row.outbound_max
        latest_inbound_text: str | None = row.latest_inbound_text
        latest_outbound_text: str | None = row.latest_outbound_text

        # Determine preview: pick text from the source with the latest timestamp
        if inbound_max is not None and (outbound_max is None or inbound_max >= outbound_max):
            preview = latest_inbound_text
        elif outbound_max is not None:
            preview = latest_outbound_text
        else:
            preview = None

        unread: int = row.unread or 0
        funnel_node: str | None = row.funnel_node

        results.append(
            ContactRow(
                lead_id=lead.id,
                tenant_id=lead.tenant_id,
                whatsapp_e164=lead.whatsapp_e164,
                display_name=lead.display_name,
                status=lead.status,
                inbound_channel_label=lead.inbound_channel_label,
                created_at=lead.created_at,
                state=derive_state(talk),
                last_message_at=last_msg_at,
                last_message_preview=preview,
                active_talk=talk,
                funnel_node=funnel_node,
                unread=unread,
            )
        )

    return results


# ---------------------------------------------------------------------------
# list_messages
# ---------------------------------------------------------------------------

async def list_messages(
    session: AsyncSession,
    *,
    lead_id: uuid.UUID,
    before: datetime | None = None,
    limit: int = 50,
) -> list[MessageRow]:
    """Return the merged inbound + outbound message stream for a lead.

    Ordered by time DESC (newest first).  Cursor: messages with timestamp
    strictly before `before`.
    """
    # Build a UNION ALL of inbound and outbound messages for this lead.
    # We select a common shape: (id, direction, text, media_type, audio_url, ts).

    inbound_q = select(
        InboundMessageRow.id,
        literal("inbound").label("direction"),
        InboundMessageRow.text.label("text"),
        InboundMessageRow.media_type,
        InboundMessageRow.audio_url,
        InboundMessageRow.received_at.label("ts"),
        literal(None).label("triggered_by"),
    ).where(InboundMessageRow.lead_id == lead_id)

    outbound_q = select(
        OutboundMessage.id,
        literal("outbound").label("direction"),
        OutboundMessage.body_text.label("text"),
        OutboundMessage.media_type,
        OutboundMessage.audio_url,
        OutboundMessage.sent_at.label("ts"),
        OutboundMessage.triggered_by,
    ).where(OutboundMessage.lead_id == lead_id)

    if before is not None:
        inbound_q = inbound_q.where(InboundMessageRow.received_at < before)
        outbound_q = outbound_q.where(OutboundMessage.sent_at < before)

    combined = union_all(inbound_q, outbound_q).subquery("msgs")

    stmt = (
        select(combined)
        .order_by(combined.c.ts.desc())
        .limit(limit)
    )

    rows = (await session.execute(stmt)).all()

    return [
        MessageRow(
            id=row.id,
            direction=row.direction,
            text=row.text,
            media_type=row.media_type,
            audio_url=row.audio_url,
            created_at=row.ts,
            triggered_by=row.triggered_by,
        )
        for row in rows
    ]
