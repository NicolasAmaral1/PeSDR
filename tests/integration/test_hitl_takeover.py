from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_takeover_then_double_takeover_409(authed_inbox_client, seeded_talk_factory, db_session):
    client, ctx = authed_inbox_client
    talk, _ = await seeded_talk_factory(lead_id=ctx["lead_id"], handling_mode="ai")
    await db_session.commit()  # make talk visible to the route's session
    slug, lead = ctx["slug"], ctx["lead_id"]
    r1 = await client.post(f"/api/console/tenants/{slug}/contacts/{lead}/takeover")
    assert r1.status_code == 200
    assert r1.json()["handling_mode"] == "human"
    r2 = await client.post(f"/api/console/tenants/{slug}/contacts/{lead}/takeover")
    assert r2.status_code == 409  # already human → conflict


async def test_release_back_to_ai(authed_inbox_client, seeded_talk_factory, db_session):
    client, ctx = authed_inbox_client
    await seeded_talk_factory(lead_id=ctx["lead_id"], handling_mode="ai")
    await db_session.commit()  # make talk visible to the route's session
    slug, lead = ctx["slug"], ctx["lead_id"]
    await client.post(f"/api/console/tenants/{slug}/contacts/{lead}/takeover")
    r = await client.post(f"/api/console/tenants/{slug}/contacts/{lead}/release")
    assert r.status_code == 200
    assert r.json()["handling_mode"] == "ai"
