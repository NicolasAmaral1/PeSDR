"""WebSocket inbox route — live operator channel for one instance.

`GET (WebSocket) /ws/instances/{instance_id}`

Handshake (all BEFORE accept):
  1. Read `pesdr_session` cookie → `verify_session_cookie` → load `User`.
  2. Load `Instance` by path id → its `tenant_id`.
  3. Verify `user.is_platform_admin` OR a `UserTenantAccess(user_id, tenant_id)`
     row exists.
  On ANY failure: `await websocket.close(code=4401)` and return (no accept).

On success: `accept()`, build a `WSConnection` (an `asyncio.Queue(maxsize=100)`
whose `offer()` does `put_nowait`/False-on-full), `hub.register(instance_id, conn)`,
then run a writer (drain queue → `send_json`) and a reader (`receive_text` loop to
detect disconnect) concurrently; when either finishes, cancel the other. FINALLY
`hub.unregister(instance_id, conn)`.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from fastapi import APIRouter, WebSocket
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.db.session import get_sessionmaker
from ai_sdr.models.instance import Instance
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess

# `_COOKIE_MAX_AGE_SECONDS` reuses the SAME session window the console
# request-side dep (auth.require_console_user) uses.
from ai_sdr.web.auth import (  # noqa: PLC2701
    _COOKIE_MAX_AGE_SECONDS,
    verify_session_cookie,
)

router = APIRouter()


class WSConnection:
    """Bridges the hub's `offer(env)` to an outbound asyncio.Queue.

    The hub calls `offer(env)` (sync) from its pubsub reader; the writer task
    drains the queue and sends frames. A full queue (slow/dead client) makes
    `offer` return False so the hub unregisters this connection.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)

    def offer(self, env: dict) -> bool:
        try:
            self.queue.put_nowait(env)
        except asyncio.QueueFull:
            return False
        return True


async def _resolve_access(instance_id: uuid.UUID, cookie_value: str | None) -> bool:
    """Return True iff the cookie maps to a User allowed to read this instance."""
    payload = verify_session_cookie(
        cookie_value or "", max_age_seconds=_COOKIE_MAX_AGE_SECONDS
    )
    if payload is None:
        return False
    try:
        user_id = uuid.UUID(payload["user_id"])
    except (KeyError, ValueError):
        return False

    sm = get_sessionmaker()
    async with sm() as db:
        user = await db.get(User, user_id)
        if user is None:
            return False

        instance = (
            await db.execute(select(Instance).where(Instance.id == instance_id))
        ).scalar_one_or_none()
        if instance is None:
            return False

        if user.is_platform_admin:
            await set_tenant_context(db, instance.tenant_id)
            return True

        granted = (
            await db.execute(
                select(UserTenantAccess).where(
                    UserTenantAccess.user_id == user.id,
                    UserTenantAccess.tenant_id == instance.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if granted is None:
            return False

        await set_tenant_context(db, instance.tenant_id)
        return True


@router.websocket("/ws/instances/{instance_id}")
async def ws_inbox(websocket: WebSocket, instance_id: uuid.UUID) -> None:
    cookie_value = websocket.cookies.get("pesdr_session")
    if not await _resolve_access(instance_id, cookie_value):
        await websocket.close(code=4401)
        return

    await websocket.accept()

    hub = websocket.app.state.inbox_hub
    conn = WSConnection()
    hub.register(instance_id, conn)

    async def _writer() -> None:
        while True:
            env = await conn.queue.get()
            await websocket.send_json(env)

    async def _reader() -> None:
        # Drain inbound frames purely to detect disconnects; clients are not
        # expected to send anything on this channel.
        while True:
            await websocket.receive_text()

    writer_task = asyncio.create_task(_writer(), name="ws_inbox_writer")
    reader_task = asyncio.create_task(_reader(), name="ws_inbox_reader")
    try:
        done, pending = await asyncio.wait(
            {writer_task, reader_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
                await task
        # Surface any non-disconnect error from the finished task(s).
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                raise exc
    finally:
        hub.unregister(instance_id, conn)
