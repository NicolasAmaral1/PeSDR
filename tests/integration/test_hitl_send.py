"""Operator SEND route — HITL, idempotent, 24h-aware.

Tests:
  1. send while talk is in 'ai' mode → 409 (must takeover first)
  2. takeover → send → 200 with outbound_id
  3. same client_message_id → same outbound_id (idempotent, no re-send)
  4. adapter raises WindowExpiredError → 422 with window-closed detail
"""
from __future__ import annotations

import uuid

import pytest

from ai_sdr.messaging.errors import WindowExpiredError

pytestmark = pytest.mark.integration


async def test_send_requires_human_then_sends_idempotent(
    authed_inbox_client_with_fake_adapter, seeded_talk_factory, db_session
):
    client, ctx = authed_inbox_client_with_fake_adapter  # app.state.adapter_registry → FakeMessagingAdapter stub
    slug, lead = ctx["slug"], ctx["lead_id"]

    # Seed a talk in 'ai' mode and make it visible to the route's session.
    await seeded_talk_factory(lead_id=lead, handling_mode="ai")
    await db_session.commit()

    cmid = str(uuid.uuid4())
    body = {"text": "oi João, sou o operador", "client_message_id": cmid}

    # --- RED: ai mode → must takeover first ---
    r_ai = await client.post(
        f"/api/console/tenants/{slug}/contacts/{lead}/send", json=body
    )
    assert r_ai.status_code == 409, r_ai.text  # "take over the conversation first"

    # --- takeover ---
    takeover_resp = await client.post(
        f"/api/console/tenants/{slug}/contacts/{lead}/takeover"
    )
    assert takeover_resp.status_code == 200, takeover_resp.text

    # --- GREEN: human mode → send succeeds ---
    r1 = await client.post(
        f"/api/console/tenants/{slug}/contacts/{lead}/send", json=body
    )
    assert r1.status_code == 200, r1.text
    first_id = r1.json()["outbound_id"]
    assert first_id is not None
    assert r1.json()["status"] == "sent"

    # --- IDEMPOTENT: same client_message_id → same outbound_id, no re-send ---
    r2 = await client.post(
        f"/api/console/tenants/{slug}/contacts/{lead}/send", json=body
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["outbound_id"] == first_id  # idempotent, no re-send


async def test_send_window_expired_returns_422(
    authed_inbox_client_with_fake_adapter, seeded_talk_factory, db_session
):
    """WindowExpiredError from adapter → 422 with informative detail (M3 coverage)."""
    client, ctx = authed_inbox_client_with_fake_adapter
    slug, lead = ctx["slug"], ctx["lead_id"]
    fake_adapter = ctx["fake_adapter"]

    # Seed a talk and takeover so we're in human mode.
    await seeded_talk_factory(lead_id=lead, handling_mode="ai")
    await db_session.commit()
    takeover_resp = await client.post(
        f"/api/console/tenants/{slug}/contacts/{lead}/takeover"
    )
    assert takeover_resp.status_code == 200, takeover_resp.text

    # Force the adapter to raise WindowExpiredError on next send.
    fake_adapter.fail_next_send(WindowExpiredError("window closed"))

    r = await client.post(
        f"/api/console/tenants/{slug}/contacts/{lead}/send",
        json={"text": "oi", "client_message_id": str(uuid.uuid4())},
    )
    assert r.status_code == 422, r.text
    assert "window" in r.json()["detail"].lower()
