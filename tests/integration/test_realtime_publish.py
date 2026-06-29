from __future__ import annotations

import json
import uuid

import pytest
import redis.asyncio as aioredis

from ai_sdr.realtime.events import channel_for, publish_inbox_event
from ai_sdr.settings import get_settings

pytestmark = pytest.mark.integration


async def test_publish_increments_seq_and_delivers_envelope():
    inst = uuid.uuid4()
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    await r.delete(f"seq:inst:{inst}")
    pubsub = r.pubsub()
    await pubsub.subscribe(channel_for(inst))
    # drain the subscribe-confirm message
    await pubsub.get_message(timeout=1.0)

    lead = uuid.uuid4()
    seq1 = await publish_inbox_event(r, instance_id=inst, type="message.created", lead_id=lead, payload={"x": 1})
    assert seq1 == 1
    msg = await pubsub.get_message(timeout=2.0, ignore_subscribe_messages=True)
    env = json.loads(msg["data"])
    assert env["seq"] == 1 and env["type"] == "message.created"
    assert env["instance_id"] == str(inst) and env["lead_id"] == str(lead)
    assert env["payload"] == {"x": 1}

    seq2 = await publish_inbox_event(r, instance_id=inst, type="talk.updated", lead_id=None, payload={})
    assert seq2 == 2
    await pubsub.aclose()
    await r.aclose()
