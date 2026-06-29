"""InboxHub — per-process connection registry + Redis-to-WebSocket fan-out.

Each operator WebSocket connection registers itself for a specific instance_id.
A single background task subscribes to the Redis pattern ``inst:*`` and fans
each published event out to every registered connection for that instance.

Connection contract (duck-typed):
    conn.offer(env: dict) -> bool
        Called by the hub for each incoming event.  Returns True on success,
        False if the connection's queue is full (hub will unregister it).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class InboxHub:
    """Connection registry with a single background Redis pubsub reader."""

    def __init__(self) -> None:
        # instance_id (UUID) -> set of connections
        self._conns: dict[uuid.UUID, set] = defaultdict(set)
        self._task: asyncio.Task | None = None
        self._pubsub = None

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def register(self, instance_id: uuid.UUID, conn: object) -> None:
        """Register *conn* to receive events for *instance_id*."""
        self._conns[instance_id].add(conn)

    def unregister(self, instance_id: uuid.UUID, conn: object) -> None:
        """Remove *conn* from the registry for *instance_id* (no-op if absent)."""
        conns = self._conns.get(instance_id)
        if conns is not None:
            conns.discard(conn)
            if not conns:
                del self._conns[instance_id]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, redis) -> None:
        """Open a pattern subscription and spawn the background reader task."""
        self._pubsub = redis.pubsub()
        await self._pubsub.psubscribe("inst:*")
        self._task = asyncio.create_task(self._reader(), name="inbox_hub_reader")

    async def stop(self) -> None:
        """Cancel the background reader and close the pubsub connection."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None

    # ------------------------------------------------------------------
    # Background reader
    # ------------------------------------------------------------------

    async def _reader(self) -> None:
        """Loop over pubsub messages and fan out to registered connections."""
        try:
            async for message in self._pubsub.listen():
                if message["type"] != "pmessage":
                    continue
                try:
                    env: dict = json.loads(message["data"])
                    inst = uuid.UUID(env["instance_id"])
                except (ValueError, KeyError, TypeError):
                    logger.warning("InboxHub: malformed message, skipping: %r", message)
                    continue

                dead: list = []
                for conn in list(self._conns.get(inst, ())):
                    try:
                        ok = conn.offer(env)
                    except Exception:
                        logger.exception("InboxHub: conn.offer raised, dropping connection")
                        ok = False
                    if not ok:
                        dead.append((inst, conn))

                for inst_id, conn in dead:
                    self.unregister(inst_id, conn)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("InboxHub._reader crashed")
