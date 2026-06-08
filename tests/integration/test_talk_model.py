"""Talk model accepts all fields and exposes typed enums."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import get_args

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import HandlingMode, Talk, TalkStatus
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion


def test_status_literal_alias() -> None:
    assert set(get_args(TalkStatus)) == {
        "active", "paused", "requires_review",
        "closed_completed", "closed_inactivity", "closed_optout", "closed_banned",
    }


def test_handling_mode_literal_alias() -> None:
    assert set(get_args(HandlingMode)) == {"ai", "human", "auto_with_approval"}


@pytest.mark.asyncio
async def test_talk_insert_round_trip(db_session: AsyncSession) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id)
    db_session.add(lead)
    tfv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="tf", version="1.0",
        content_hash="x", content_yaml="yaml",
    )
    db_session.add(tfv)
    await db_session.flush()

    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )

    talk = Talk(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_id="tf",
        treeflow_version_id=tfv.id,
        status="active",
        handling_mode="ai",
        last_message_at=datetime.now(timezone.utc),
    )
    db_session.add(talk)
    await db_session.flush()

    fetched = (await db_session.execute(select(Talk).where(Talk.id == talk.id))).scalar_one()
    assert fetched.status == "active"
    assert fetched.handling_mode == "ai"
    assert fetched.turn_count == 0
    assert fetched.tokens_consumed == {}
