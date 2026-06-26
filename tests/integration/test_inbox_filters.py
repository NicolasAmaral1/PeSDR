"""Integration tests for inbox contact filters + read→unread roundtrip.

Task 6: status filter (awaiting) and read→unread=0 roundtrip.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration


async def test_status_filter_awaiting(authed_inbox_client, db_session):
    """?status=awaiting returns fresh leads and excludes leads with a closed talk.

    Seeds a second lead (same tenant/instance) that has a closed talk.
    Asserts:
      - ?status=awaiting includes the fresh lead but NOT the closed-talk lead.
      - ?status=closed  includes the closed-talk lead with state=="closed".
    """
    client, ctx = authed_inbox_client
    tenant = ctx["tenant"]
    fresh_lead = ctx["lead"]

    # Re-establish RLS context (fixture committed; we're in a new implicit TX).
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )

    # Seed a TreeflowVersion so Talk FK is satisfied.
    tfv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="tf-test",
        version="1",
        content_hash=uuid.uuid4().hex,
        content_yaml="nodes: []",
    )
    db_session.add(tfv)
    await db_session.flush()

    # Seed a second lead with a closed talk.
    closed_lead = Lead(
        tenant_id=tenant.id,
        whatsapp_e164="+5511900000001",
        status="pending_assignment",
        inbound_channel_label="main",
    )
    db_session.add(closed_lead)
    await db_session.flush()

    closed_talk = Talk(
        tenant_id=tenant.id,
        lead_id=closed_lead.id,
        treeflow_id="tf-test",
        treeflow_version_id=tfv.id,
        status="closed_completed",
        handling_mode="ai",
        last_message_at=datetime.now(UTC),
    )
    db_session.add(closed_talk)
    await db_session.commit()

    # Fetch instance id.
    insts = (
        await client.get(f"/api/console/tenants/{ctx['slug']}/instances")
    ).json()
    main_id = next(i["id"] for i in insts if i["channel_label"] == "main")

    # --- awaiting filter ---
    resp_awaiting = await client.get(
        f"/api/console/tenants/{ctx['slug']}/instances/{main_id}/contacts?status=awaiting"
    )
    assert resp_awaiting.status_code == 200
    awaiting_contacts = resp_awaiting.json()

    awaiting_ids = {c["lead_id"] for c in awaiting_contacts}
    assert str(fresh_lead.id) in awaiting_ids, "Fresh lead must appear in awaiting"
    assert str(closed_lead.id) not in awaiting_ids, (
        "Lead with closed talk must NOT appear in awaiting"
    )
    assert all(c["state"] == "awaiting" for c in awaiting_contacts), (
        "All awaiting contacts must have state=='awaiting'"
    )

    # --- closed filter ---
    resp_closed = await client.get(
        f"/api/console/tenants/{ctx['slug']}/instances/{main_id}/contacts?status=closed"
    )
    assert resp_closed.status_code == 200
    closed_contacts = resp_closed.json()

    closed_ids = {c["lead_id"] for c in closed_contacts}
    assert str(closed_lead.id) in closed_ids, "Lead with closed talk must appear in closed"
    assert str(fresh_lead.id) not in closed_ids, "Fresh lead must NOT appear in closed"

    closed_lead_row = next(c for c in closed_contacts if c["lead_id"] == str(closed_lead.id))
    assert closed_lead_row["state"] == "closed", (
        f"Closed-talk lead must have state=='closed', got {closed_lead_row['state']!r}"
    )


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
