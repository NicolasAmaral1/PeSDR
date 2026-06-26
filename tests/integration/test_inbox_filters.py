"""Integration tests for inbox contact filters + read→unread roundtrip.

Task 6: status filter (awaiting) and read→unread=0 roundtrip.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_status_filter_awaiting(authed_inbox_client):
    """Contacts filtered by status=awaiting must all have state == 'awaiting'."""
    client, ctx = authed_inbox_client
    insts = (await client.get(f"/api/console/tenants/{ctx['slug']}/instances")).json()
    main_id = next(i["id"] for i in insts if i["channel_label"] == "main")
    resp = await client.get(
        f"/api/console/tenants/{ctx['slug']}/instances/{main_id}/contacts?status=awaiting"
    )
    assert resp.status_code == 200
    assert all(c["state"] == "awaiting" for c in resp.json())


async def test_read_then_unread_zero(authed_inbox_client):
    """After posting read with the latest message timestamp, unread count drops to 0."""
    client, ctx = authed_inbox_client
    lead_id = ctx["lead_id"]
    msgs = (await client.get(f"/api/console/tenants/{ctx['slug']}/contacts/{lead_id}/messages")).json()
    latest = max(m["at"] for m in msgs)
    r = await client.post(
        f"/api/console/tenants/{ctx['slug']}/contacts/{lead_id}/read",
        json={"last_read_message_at": latest},
    )
    assert r.status_code == 204
    insts = (await client.get(f"/api/console/tenants/{ctx['slug']}/instances")).json()
    main_id = next(i["id"] for i in insts if i["channel_label"] == "main")
    contacts = (await client.get(f"/api/console/tenants/{ctx['slug']}/instances/{main_id}/contacts")).json()
    c = next(c for c in contacts if c["lead_id"] == str(lead_id))
    assert c["unread"] == 0
