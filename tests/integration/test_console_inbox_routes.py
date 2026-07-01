"""Auth/RLS scaffolding mirrors tests/integration/test_console_leads_page.py.

The authed_inbox_client fixture is defined in conftest.py and shared with
test_inbox_filters.py.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_list_instances_returns_main(authed_inbox_client):
    client, ctx = authed_inbox_client  # fixture: signed-in client + seeded tenant w/ console enabled
    resp = await client.get(f"/api/console/tenants/{ctx['slug']}/instances")
    assert resp.status_code == 200
    labels = [i["channel_label"] for i in resp.json()]
    assert "main" in labels


async def test_contacts_lists_lead_without_talk(authed_inbox_client):
    client, ctx = authed_inbox_client
    # ctx seeded a lead 'pending_assignment' with one queued inbound on channel 'main'
    instances = (await client.get(f"/api/console/tenants/{ctx['slug']}/instances")).json()
    main_id = next(i["id"] for i in instances if i["channel_label"] == "main")
    resp = await client.get(f"/api/console/tenants/{ctx['slug']}/instances/{main_id}/contacts")
    assert resp.status_code == 200
    body = resp.json()
    assert any(c["state"] == "awaiting" and c["unread"] >= 1 for c in body)
