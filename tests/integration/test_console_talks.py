from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_talks_returns_bands_for_lead(authed_inbox_client, seeded_talk_factory, db_session):
    client, ctx = authed_inbox_client
    await seeded_talk_factory(lead_id=ctx["lead_id"], handling_mode="ai", status="active")
    await db_session.commit()
    resp = await client.get(f"/api/console/tenants/{ctx['slug']}/contacts/{ctx['lead_id']}/talks")
    assert resp.status_code == 200
    bands = resp.json()
    assert len(bands) >= 1
    assert {"talk_id", "status", "funnel_node", "created_at"} <= set(bands[0].keys())


async def test_talks_cross_tenant_404(authed_inbox_client):
    client, ctx = authed_inbox_client
    import uuid
    resp = await client.get(f"/api/console/tenants/{ctx['slug']}/contacts/{uuid.uuid4()}/talks")
    assert resp.status_code == 404
