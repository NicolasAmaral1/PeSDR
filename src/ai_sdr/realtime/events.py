"""Inbox realtime events — publish to Redis with a per-instance monotonic seq."""

from __future__ import annotations

import json
import uuid


def channel_for(instance_id: uuid.UUID | str) -> str:
    return f"inst:{instance_id}"


async def publish_inbox_event(
    redis,
    *,
    instance_id: uuid.UUID,
    type: str,
    lead_id: uuid.UUID | None,
    payload: dict,
) -> int:
    """INCR the instance seq, publish the envelope, return the seq."""
    seq = await redis.incr(f"seq:inst:{instance_id}")
    envelope = {
        "seq": int(seq),
        "type": type,
        "instance_id": str(instance_id),
        "lead_id": str(lead_id) if lead_id is not None else None,
        "payload": payload,
    }
    await redis.publish(channel_for(instance_id), json.dumps(envelope))
    return int(seq)
