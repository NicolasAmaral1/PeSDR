"""Integration tests: HITL routes publish realtime events after DB commit."""

from __future__ import annotations

import json
import uuid

import pytest
import redis.asyncio as aioredis

from ai_sdr.realtime.events import channel_for
from ai_sdr.realtime.producers import resolve_instance_id
from ai_sdr.settings import get_settings

pytestmark = pytest.mark.integration


async def test_takeover_publishes_talk_updated(
    app, authed_inbox_client_with_fake_adapter, seeded_talk_factory, db_session
):
    client, ctx = authed_inbox_client_with_fake_adapter
    await seeded_talk_factory(lead_id=ctx["lead_id"], handling_mode="ai")
    await db_session.commit()
    inst_id = await resolve_instance_id(
        db_session, tenant_id=ctx["tenant_id"], channel_label="main"
    )
    assert inst_id is not None, "Instance with channel_label='main' must exist for this tenant"

    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    # Inject redis into app.state so the route's getattr guard finds it.
    app.state.redis = r

    pubsub = r.pubsub()
    await pubsub.subscribe(channel_for(inst_id))
    # Drain the subscribe-confirmation message
    await pubsub.get_message(timeout=1.0)

    resp = await client.post(
        f"/api/console/tenants/{ctx['slug']}/contacts/{ctx['lead_id']}/takeover"
    )
    assert resp.status_code == 200

    got = None
    for _ in range(20):
        m = await pubsub.get_message(timeout=0.5, ignore_subscribe_messages=True)
        if m:
            got = m
            break

    assert got is not None, "expected a talk.updated event on the Redis channel"
    data = json.loads(got["data"])
    assert data["type"] == "talk.updated"
    assert data["payload"]["handling_mode"] == "human"
    assert data["payload"]["lead_id"] == str(ctx["lead_id"])

    await pubsub.aclose()
    await r.aclose()
    # Clean up so we don't leak redis into other tests
    del app.state.redis


async def test_send_publishes_message_created(
    app, authed_inbox_client_with_fake_adapter, seeded_talk_factory, db_session
):
    """After operator send, a message.created event is published on the instance channel."""
    client, ctx = authed_inbox_client_with_fake_adapter
    await seeded_talk_factory(lead_id=ctx["lead_id"], handling_mode="human")
    await db_session.commit()
    inst_id = await resolve_instance_id(
        db_session, tenant_id=ctx["tenant_id"], channel_label="main"
    )
    assert inst_id is not None

    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    app.state.redis = r

    pubsub = r.pubsub()
    await pubsub.subscribe(channel_for(inst_id))
    await pubsub.get_message(timeout=1.0)

    resp = await client.post(
        f"/api/console/tenants/{ctx['slug']}/contacts/{ctx['lead_id']}/send",
        json={"text": "Hello lead!", "client_message_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 200

    got = None
    for _ in range(20):
        m = await pubsub.get_message(timeout=0.5, ignore_subscribe_messages=True)
        if m and json.loads(m["data"])["type"] == "message.created":
            got = m
            break

    assert got is not None, "expected a message.created event on the Redis channel"
    data = json.loads(got["data"])
    assert data["payload"]["lead_id"] == str(ctx["lead_id"])

    await pubsub.aclose()
    await r.aclose()
    del app.state.redis
