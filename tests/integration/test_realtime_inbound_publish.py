"""Integration test — publish_message_created fires a message.created event on Redis pubsub."""

from __future__ import annotations

import uuid

import pytest
import redis.asyncio as aioredis

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.instance import Instance
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.realtime.events import channel_for
from ai_sdr.realtime.producers import publish_message_created, resolve_instance_id
from ai_sdr.settings import get_settings

pytestmark = pytest.mark.integration


async def test_publish_message_created_for_lead(db_session, seeded_talk_factory):
    """publish_message_created sends a message.created event visible via pubsub."""
    # Seed tenant + talk + lead with inbound_channel_label='main' (default).
    talk, tenant = await seeded_talk_factory(handling_mode="ai")

    # Ensure an Instance row exists for this tenant + channel_label='main'.
    # seeded_talk_factory does NOT create an Instance, so we add it here.
    await set_tenant_context(db_session, tenant.id)
    instance = Instance(tenant_id=tenant.id, channel_label="main", display_name="Main")
    db_session.add(instance)
    await db_session.flush()

    # Verify resolve_instance_id finds the instance.
    inst_id = await resolve_instance_id(
        db_session, tenant_id=tenant.id, channel_label="main"
    )
    assert inst_id is not None, "resolve_instance_id should find the seeded Instance"
    assert inst_id == instance.id

    # Open a pubsub subscription BEFORE publishing to avoid a race.
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(channel_for(inst_id))
    # Drain the subscribe-confirmation message.
    await pubsub.get_message(timeout=1.0)

    # Load the lead created by the factory.
    lead = await db_session.get(Lead, talk.lead_id)
    assert lead is not None
    assert lead.inbound_channel_label == "main"

    # Publish — expects at least one message.created event on the channel.
    await publish_message_created(
        r, db_session, tenant_id=tenant.id, lead=lead, body_preview="oi"
    )

    got = []
    for _ in range(20):
        m = await pubsub.get_message(timeout=0.5, ignore_subscribe_messages=True)
        if m:
            got.append(m)
        if len(got) >= 1:
            break

    assert got, "expected at least one event on the instance channel"

    # Validate the first event has the expected type.
    import json
    first = json.loads(got[0]["data"])
    assert first["type"] == "message.created"
    assert first["instance_id"] == str(inst_id)
    assert first["lead_id"] == str(lead.id)
    assert first["payload"]["lead_id"] == str(lead.id)
    assert first["payload"]["preview"] == "oi"

    await pubsub.aclose()
    await r.aclose()


async def test_resolve_instance_id_returns_none_for_unknown_channel(db_session, seeded_talk_factory):
    """resolve_instance_id returns None when no matching Instance exists."""
    talk, tenant = await seeded_talk_factory(handling_mode="ai")
    await set_tenant_context(db_session, tenant.id)

    result = await resolve_instance_id(
        db_session, tenant_id=tenant.id, channel_label="nonexistent"
    )
    assert result is None


async def test_publish_message_created_noop_when_no_instance(db_session, seeded_talk_factory):
    """publish_message_created does not raise when no Instance exists."""
    talk, tenant = await seeded_talk_factory(handling_mode="ai")
    await set_tenant_context(db_session, tenant.id)

    lead = await db_session.get(Lead, talk.lead_id)
    # lead.inbound_channel_label='main' but no Instance row exists.
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    # Should not raise — must no-op gracefully.
    await publish_message_created(
        r, db_session, tenant_id=tenant.id, lead=lead, body_preview="test"
    )
    await r.aclose()
