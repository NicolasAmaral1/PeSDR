"""Tenant carries an architecture_version feature flag."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.tenant import Tenant


@pytest.mark.asyncio
async def test_tenant_architecture_version_defaults_to_1(
    db_session: AsyncSession,
) -> None:
    t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(t)
    await db_session.flush()
    fetched = (await db_session.execute(select(Tenant).where(Tenant.id == t.id))).scalar_one()
    assert fetched.architecture_version == 1


@pytest.mark.asyncio
async def test_tenant_architecture_version_can_be_set_to_2(
    db_session: AsyncSession,
) -> None:
    t = Tenant(
        slug=f"t-{uuid.uuid4().hex[:8]}",
        display_name="t",
        architecture_version=2,
    )
    db_session.add(t)
    await db_session.flush()
    assert t.architecture_version == 2
