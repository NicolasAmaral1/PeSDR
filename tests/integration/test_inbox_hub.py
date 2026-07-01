from __future__ import annotations

import asyncio
import uuid

import pytest
import redis.asyncio as aioredis

from ai_sdr.realtime.events import publish_inbox_event
from ai_sdr.realtime.hub import InboxHub
from ai_sdr.settings import get_settings

pytestmark = pytest.mark.integration


class _RecordingConn:
    def __init__(self):
        self.received: list[dict] = []

    def offer(self, env: dict) -> bool:
        self.received.append(env)
        return True


async def test_hub_forwards_published_event_to_registered_conn():
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    hub = InboxHub()
    await hub.start(r)
    inst = uuid.uuid4()
    conn = _RecordingConn()
    hub.register(inst, conn)
    try:
        await publish_inbox_event(r, instance_id=inst, type="message.created", lead_id=None, payload={"hi": 1})
        # give the pubsub reader a moment to route it
        for _ in range(50):
            if conn.received:
                break
            await asyncio.sleep(0.05)
        assert conn.received and conn.received[0]["type"] == "message.created"
        assert conn.received[0]["payload"] == {"hi": 1}
    finally:
        hub.unregister(inst, conn)
        await hub.stop()
        await r.aclose()
