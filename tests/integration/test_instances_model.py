# tests/integration/test_instances_model.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from ai_sdr.models.instance import Instance
from ai_sdr.models.tenant import Tenant

pytestmark = pytest.mark.integration


async def test_instance_insert_and_rls_scoping(db_session):
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T", architecture_version=2)
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant.id)}
    )
    inst = Instance(tenant_id=tenant.id, channel_label="main", display_name="T")
    db_session.add(inst)
    await db_session.flush()

    rows = (await db_session.execute(select(Instance).where(Instance.tenant_id == tenant.id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].channel_label == "main"
