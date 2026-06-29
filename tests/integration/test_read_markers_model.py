from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ai_sdr.models.lead import Lead
from ai_sdr.models.operator_read_marker import OperatorReadMarker
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User

pytestmark = pytest.mark.integration


async def test_read_marker_upsert(db_session):
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T", architecture_version=2)
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant.id)}
    )

    user = User(username=f"u_{uuid.uuid4().hex[:6]}", password_hash="$2b$12$" + "x" * 53)
    db_session.add(user)
    await db_session.flush()

    lead = Lead(tenant_id=tenant.id)
    db_session.add(lead)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    stmt = pg_insert(OperatorReadMarker).values(
        tenant_id=tenant.id, user_id=user.id, lead_id=lead.id, last_read_at=now, last_read_message_at=now
    ).on_conflict_do_update(
        index_elements=["user_id", "lead_id"],
        set_={"last_read_at": now, "last_read_message_at": now},
    )
    await db_session.execute(stmt)
    await db_session.execute(stmt)  # idempotent upsert
    row = await db_session.get(OperatorReadMarker, {"user_id": user.id, "lead_id": lead.id})
    assert row is not None
