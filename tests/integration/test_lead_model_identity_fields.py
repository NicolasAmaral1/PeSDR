"""Lead model exposes the FlowEngine identity fields."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant


@pytest.mark.asyncio
async def test_lead_model_has_identity_fields(db_session: AsyncSession) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()

    lead = Lead(
        tenant_id=tenant.id,
        channel_identifiers={"whatsapp": "+5511999999999"},
        display_name="Test",
        profile={"likes": "coffee"},
        long_term_memory_enabled=False,
        risk_level="normal",
        acquisition_metadata={"utm_source": "google"},
    )
    db_session.add(lead)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(Lead).where(Lead.id == lead.id))
    ).scalar_one()
    assert fetched.channel_identifiers == {"whatsapp": "+5511999999999"}
    assert fetched.display_name == "Test"
    assert fetched.profile == {"likes": "coffee"}
    assert fetched.long_term_memory_enabled is False
    assert fetched.risk_level == "normal"
    assert fetched.acquisition_metadata == {"utm_source": "google"}
    assert fetched.profile_last_updated is None
    assert fetched.risk_level_since is None
    assert fetched.risk_level_reason is None


@pytest.mark.asyncio
async def test_lead_model_risk_level_typed(db_session: AsyncSession) -> None:
    """RiskLevel literal type rejects unknown values at static checking."""
    from typing import get_args
    from ai_sdr.models.lead import RiskLevel

    assert set(get_args(RiskLevel)) == {"normal", "elevated", "banned"}
