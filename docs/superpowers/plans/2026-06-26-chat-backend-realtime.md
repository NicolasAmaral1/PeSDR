# Chat Backend — Realtime (Plano 2B-i) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push live inbox updates to operators over WebSocket — new inbound/outbound messages and talk-state changes appear in real time, ordered by a monotonic per-instance sequence number.

**Architecture:** A Redis pub/sub bus (`inst:{instance_id}` channels) carries event envelopes with a monotonic `seq` (Redis `INCR seq:inst:{id}`). Producers (webhook ingest, operator send route, takeover/release, and the arq worker that persists the AI reply) call a shared `publish_inbox_event` helper. Each API process runs one `InboxHub` that pattern-subscribes (`psubscribe inst:*`) and fans each event out to the local WebSocket connections registered for that instance. A WS route `/ws/instances/{instance_id}` authenticates via the existing session cookie + tenant access and streams events.

**Tech Stack:** Python 3.12, FastAPI + Starlette WebSockets, `redis.asyncio` pub/sub, arq (worker), pytest (`pytest.mark.integration`) with Starlette `TestClient.websocket_connect`, uv. Builds on Planos 1+2A (branch `dev/nicolas-chat-frontend`, DB at head `0036`).

## Global Constraints

- **Redis is the cross-process bus.** The API process and the arq worker are separate processes; the worker publishes to Redis and the API's `InboxHub` (a subscriber) delivers to browsers. Redis URL = `get_settings().redis_url` (test env: `localhost:16379` via the tunnel).
- **Event envelope (exact shape):** `{"seq": int, "type": str, "instance_id": str, "lead_id": str | None, "payload": dict}`. Event types: `message.created`, `talk.updated`, `contact.updated`. (`message.status_updated` + `talk.window_expired` land in Plano 2B-ii.) JSON-serialized.
- **`seq` is per-instance monotonic** via `INCR seq:inst:{instance_id}`, stamped at publish time. It orders live events and lets the client dedup.
- **Reconnect (deliberate v1 simplification):** Redis pub/sub has NO history. On reconnect the CLIENT (Plano 3) re-fetches current state via the Plano-1 REST read API and dedups live events by message id + `seq`. The server does NOT replay missed events from history (true server-side seq-replay needs a persisted events table — deferred to a later plan). The WS accepts an optional `last_seq` query param and uses it ONLY to let the client discard already-seen live events; it is not a replay cursor. Document this in the WS route docstring.
- **Instance resolution:** an "instance" = `(tenant_id, channel_label)` materialized as the `instances` table (Plano 1). Producers know `tenant_id` + `channel_label` (or a `lead_id` whose `inbound_channel_label` gives the channel); resolve to `instance_id` and publish on `inst:{instance_id}`.
- **WS auth:** reuse the session cookie. `websocket.cookies.get("pesdr_session")` → `verify_session_cookie` → resolve `User` → verify access to the instance's tenant (`UserTenantAccess` or `is_platform_admin`). Reject with `await websocket.close(code=4401)` on failure (4401 = app-level unauthorized).
- **Tenant safety:** the WS handshake verifies the user can access the instance's tenant before subscribing; an operator can only receive events for instances of tenants they have access to.
- **Backpressure:** per-connection bounded `asyncio.Queue(maxsize=100)`; on overflow, drop the connection (send a final `{"type":"overflow"}` best-effort, then close) — the client reconnects + refetches.
- **Env:** test DB + Redis ready (`.env` + tunnel @ 15432/16379, alembic `0036`). If tunnel refused: `pkill -f "ssh.*15432"; ssh -fN -L 15432:localhost:15432 -L 16379:localhost:16379 vps-nova`. Run integration separately from unit.
- **TDD; frequent commits.** Commit messages: `feat(chat-rt): …`.

---

### Task 1: `publish_inbox_event` helper (seq + Redis publish)

**Files:**
- Create: `src/ai_sdr/realtime/__init__.py`, `src/ai_sdr/realtime/events.py`
- Test: `tests/integration/test_realtime_publish.py`

**Interfaces:**
- Produces: `async publish_inbox_event(redis, *, instance_id: uuid.UUID, type: str, lead_id: uuid.UUID | None, payload: dict) -> int` — `INCR seq:inst:{instance_id}`, builds the envelope `{seq,type,instance_id,lead_id,payload}`, `await redis.publish(f"inst:{instance_id}", json.dumps(envelope))`, returns the `seq`. `redis` is a `redis.asyncio.Redis` (decode_responses=True). Also `def channel_for(instance_id) -> str` returning `f"inst:{instance_id}"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_realtime_publish.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_realtime_publish.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_sdr.realtime`.

- [ ] **Step 3: Write the helper**

```python
# src/ai_sdr/realtime/__init__.py
```

```python
# src/ai_sdr/realtime/events.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_realtime_publish.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/realtime tests/integration/test_realtime_publish.py
git commit -m "feat(chat-rt): publish_inbox_event helper (seq + redis publish)"
```

---

### Task 2: `InboxHub` — connection registry + Redis-to-WS fan-out

**Files:**
- Create: `src/ai_sdr/realtime/hub.py`
- Test: `tests/integration/test_inbox_hub.py`

**Interfaces:**
- Produces: `class InboxHub` with:
  - `def register(instance_id: uuid.UUID, conn: "Connection") -> None` / `def unregister(instance_id, conn)`.
  - `async def start(redis) -> None` — opens a pattern subscription `psubscribe inst:*` and spawns a background reader task that, for each message, parses the envelope, reads `instance_id`, and calls `conn.send(envelope)` on every registered connection for that instance (drop on a full queue).
  - `async def stop() -> None`.
  - A `Connection` protocol/dataclass with `async def send(envelope: dict)` (the WS route provides one backed by an `asyncio.Queue(maxsize=100)`).

> Implementation: `start()` does `self._pubsub = redis.pubsub(); await self._pubsub.psubscribe("inst:*"); self._task = asyncio.create_task(self._reader())`. `_reader()` loops `async for message in self._pubsub.listen():` (skip non-`pmessage`), `env = json.loads(message["data"])`, `inst = uuid.UUID(env["instance_id"])`, then for each conn in `self._conns.get(inst, ())`: `await conn.send(env)` — but make the per-connection send non-blocking/drop-on-full (call a `conn.offer(env)` that does `queue.put_nowait` and on `QueueFull` marks the conn dead). Keep the hub a per-process singleton stored on `app.state.inbox_hub`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_inbox_hub.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_inbox_hub.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_sdr.realtime.hub`.

- [ ] **Step 3: Write the hub**

Implement `InboxHub` per the interface: `register`/`unregister` (dict `instance_id -> set[conn]`), `start(redis)` (psubscribe `inst:*` + reader task), `_reader` routing by `instance_id` calling `conn.offer(env)`, `stop()` (cancel task + `aclose` pubsub). The `Connection` contract is `offer(env: dict) -> bool` (returns False if its queue is full → hub calls `unregister`). For Task 2's test a recording stub suffices.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_inbox_hub.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/realtime/hub.py tests/integration/test_inbox_hub.py
git commit -m "feat(chat-rt): InboxHub redis-to-connection fan-out"
```

---

### Task 3: WS route `/ws/instances/{instance_id}` (cookie auth + hub registration) + lifespan wiring

**Files:**
- Create: `src/ai_sdr/api/routes/ws_inbox.py`
- Modify: `src/ai_sdr/main.py` (lifespan: create `app.state.redis` + `app.state.inbox_hub`; register the WS route)
- Test: `tests/integration/test_ws_inbox_route.py`

**Interfaces:**
- Consumes: `publish_inbox_event` (T1), `InboxHub` (T2), `verify_session_cookie` (`ai_sdr.web.auth`), `Instance`/`UserTenantAccess`/`User` models.
- Produces: `GET (WebSocket) /ws/instances/{instance_id}` — handshake: read `pesdr_session` cookie → `verify_session_cookie` → load `User`; load `Instance` by id → its `tenant_id`; verify `user.is_platform_admin or UserTenantAccess(user_id, tenant_id)` exists; on any failure `await websocket.close(code=4401)`. On success: `await websocket.accept()`, build a `WSConnection` (wraps an `asyncio.Queue(maxsize=100)`, `offer()` does `put_nowait`/False-on-full), `hub.register(instance_id, conn)`, then run two tasks: a writer draining the queue → `await websocket.send_json(env)`, and a reader `await websocket.receive_text()` loop (to detect disconnect). On disconnect/overflow: `hub.unregister`, close. The lifespan creates `app.state.redis = aioredis.from_url(redis_url, decode_responses=True)` and `app.state.inbox_hub = InboxHub(); await hub.start(app.state.redis)`, and `await hub.stop(); await app.state.redis.aclose()` on shutdown.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_ws_inbox_route.py
"""WS handshake auth + live delivery. Mirrors the cookie-auth pattern of
tests/integration/test_console_leads_page.py for seeding User+access+cookie."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


async def test_ws_receives_published_event(ws_authed_ctx):
    # ws_authed_ctx: an authenticated Starlette TestClient + a seeded instance_id
    # + a redis client + the app (with lifespan run so app.state.redis/inbox_hub exist).
    client, ctx = ws_authed_ctx
    inst = ctx["instance_id"]
    with client.websocket_connect(
        f"/ws/instances/{inst}", cookies={"pesdr_session": ctx["cookie"]}
    ) as ws:
        await ctx["publish"](type="talk.updated", lead_id=None, payload={"status": "requires_review"})
        data = ws.receive_json()
        assert data["type"] == "talk.updated"
        assert data["payload"]["status"] == "requires_review"


async def test_ws_rejects_unauthenticated(ws_authed_ctx):
    client, ctx = ws_authed_ctx
    inst = ctx["instance_id"]
    with pytest.raises(Exception):  # close(4401) surfaces as a WS connect failure
        with client.websocket_connect(f"/ws/instances/{inst}"):  # no cookie
            pass
```

> **Build the `ws_authed_ctx` fixture** (in `tests/integration/conftest.py`): use the SYNCHRONOUS Starlette `TestClient(app)` (its `websocket_connect` is sync and runs the app's lifespan, so `app.state.redis`/`inbox_hub` get created). Seed (via a committed DB session) a Tenant whose on-disk config has `console.enabled=true` (or monkeypatch tenant loader as `test_console_leads_page.py` does), a `User` + `UserTenantAccess`, and an `Instance` (channel_label='main'); sign the cookie via `sign_session_cookie(user.id)`. Provide `ctx["publish"]` = a helper that calls `publish_inbox_event(redis, instance_id=..., ...)` against the SAME redis the app's hub subscribes to. Mirror `test_console_leads_page.py` for the tenant-config + cookie parts. NOTE: because `TestClient` is sync, write these two tests as sync `def` (not async) and drive the async publish via `client.portal`/`anyio` or expose `publish` as a sync wrapper — the implementer resolves the sync/async bridge (Starlette TestClient runs the app in a portal thread; use `client.portal.call(...)` or a synchronous redis client for the test publish).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_ws_inbox_route.py -q`
Expected: FAIL — route 404 / fixture missing.

- [ ] **Step 3: Implement the WS route + lifespan**

Implement `ws_inbox.py` per the interface; wire `app.state.redis` + `app.state.inbox_hub` in `main.py`'s lifespan and `app.add_api_websocket_route("/ws/instances/{instance_id}", ws_inbox_endpoint)` (or an `APIRouter` with `@router.websocket`). The endpoint accesses `websocket.app.state.inbox_hub` + `websocket.app.state.redis`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_ws_inbox_route.py -q`
Expected: PASS (2 passed: live delivery + unauth rejection).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/api/routes/ws_inbox.py src/ai_sdr/main.py tests/integration/test_ws_inbox_route.py tests/integration/conftest.py
git commit -m "feat(chat-rt): WS inbox route (cookie auth) + hub/redis lifespan"
```

---

### Task 4: Publish on inbound (webhook) + a shared resolve-instance helper

**Files:**
- Create: `src/ai_sdr/realtime/producers.py`
- Modify: `src/ai_sdr/api/routes/webhooks.py` (publish after persist)
- Test: `tests/integration/test_realtime_inbound_publish.py`

**Interfaces:**
- Consumes: `publish_inbox_event` (T1), `Instance`, `Lead`.
- Produces: `async def resolve_instance_id(session, *, tenant_id, channel_label) -> uuid.UUID | None` (looks up `instances` by `(tenant_id, channel_label)`); `async def publish_message_created(redis, session, *, tenant_id, lead, body_preview) -> None` (resolves the lead's instance via `lead.inbound_channel_label`, publishes `message.created` with payload `{lead_id, preview}` and a `contact.updated`). Webhook calls it for each affected lead after `db.commit()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_realtime_inbound_publish.py
from __future__ import annotations

import uuid

import pytest
import redis.asyncio as aioredis

from ai_sdr.realtime.events import channel_for
from ai_sdr.realtime.producers import publish_message_created, resolve_instance_id
from ai_sdr.settings import get_settings

pytestmark = pytest.mark.integration


async def test_publish_message_created_for_lead(db_session, seeded_talk_factory):
    # seed a tenant + instance(main) + lead on channel 'main'
    talk, tenant = await seeded_talk_factory(handling_mode="ai")
    # ensure an instance row + the lead's channel_label='main' (seed/adjust as needed)
    inst_id = await resolve_instance_id(db_session, tenant_id=tenant.id, channel_label="main")
    assert inst_id is not None
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    pubsub = r.pubsub(); await pubsub.subscribe(channel_for(inst_id))
    await pubsub.get_message(timeout=1.0)
    lead = await db_session.get(type(talk).__mro__[0], talk.lead_id)  # load the Lead
    # (the factory created the lead; load it for the helper)
    from ai_sdr.models.lead import Lead
    lead = await db_session.get(Lead, talk.lead_id)
    await publish_message_created(r, db_session, tenant_id=tenant.id, lead=lead, body_preview="oi")
    got = []
    for _ in range(20):
        m = await pubsub.get_message(timeout=0.5, ignore_subscribe_messages=True)
        if m: got.append(m)
        if len(got) >= 1: break
    assert got, "expected a message.created event"
    await pubsub.aclose(); await r.aclose()
```

> The factory must ensure an `Instance(channel_label='main')` exists for the tenant and the lead's `inbound_channel_label='main'`. If `seeded_talk_factory` doesn't create the instance, seed it in the test before resolving.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_realtime_inbound_publish.py -q`
Expected: FAIL — `ai_sdr.realtime.producers` missing.

- [ ] **Step 3: Implement producers + wire the webhook**

Implement `producers.py` (`resolve_instance_id`, `publish_message_created`). In `webhooks.py`, after the `for lead_id in affected_lead_ids: enqueue` loop (post-`db.commit()`), get the redis client (`request.app.state.redis`) and call `publish_message_created(...)` per affected lead (load the Lead). Keep it best-effort (wrap in try/except, log on failure — a publish failure must NOT fail the webhook).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_realtime_inbound_publish.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/realtime/producers.py src/ai_sdr/api/routes/webhooks.py tests/integration/test_realtime_inbound_publish.py
git commit -m "feat(chat-rt): publish message.created/contact.updated on inbound webhook"
```

---

### Task 5: Publish on operator send + takeover/release

**Files:**
- Modify: `src/ai_sdr/api/routes/console_inbox.py` (send → message.created; takeover/release → talk.updated)
- Test: `tests/integration/test_realtime_hitl_publish.py`

**Interfaces:**
- Consumes: `publish_message_created` / `publish_inbox_event` + `resolve_instance_id` (T4), `request.app.state.redis`.
- Produces: after the operator send commits, publish `message.created` (the operator's outbound). After takeover/release commit, publish `talk.updated` with `{lead_id, handling_mode, state}`. All best-effort (publish failure must not fail the request).

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_realtime_hitl_publish.py
from __future__ import annotations

import uuid

import pytest
import redis.asyncio as aioredis

from ai_sdr.realtime.events import channel_for
from ai_sdr.realtime.producers import resolve_instance_id
from ai_sdr.settings import get_settings

pytestmark = pytest.mark.integration


async def test_takeover_publishes_talk_updated(authed_inbox_client_with_fake_adapter, seeded_talk_factory, db_session):
    client, ctx = authed_inbox_client_with_fake_adapter
    await seeded_talk_factory(lead_id=ctx["lead_id"], handling_mode="ai")
    await db_session.commit()
    inst_id = await resolve_instance_id(db_session, tenant_id=ctx["tenant_id"], channel_label="main")
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    pubsub = r.pubsub(); await pubsub.subscribe(channel_for(inst_id)); await pubsub.get_message(timeout=1.0)
    resp = await client.post(f"/api/console/tenants/{ctx['slug']}/contacts/{ctx['lead_id']}/takeover")
    assert resp.status_code == 200
    got = None
    for _ in range(20):
        m = await pubsub.get_message(timeout=0.5, ignore_subscribe_messages=True)
        if m: got = m; break
    assert got is not None  # a talk.updated event was published
    await pubsub.aclose(); await r.aclose()
```

> Extend `authed_inbox_client_with_fake_adapter` / its `ctx` to expose `ctx["tenant_id"]` and ensure an `Instance(channel_label='main')` exists for the tenant (so `resolve_instance_id` returns it).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_realtime_hitl_publish.py -q`
Expected: FAIL — no event published.

- [ ] **Step 3: Wire publishes into the HITL routes**

In `console_inbox.py`, after the send INSERT commits, publish `message.created`; after takeover/release commit, publish `talk.updated`. Get redis from `request.app.state.redis` (add a `request: Request` param or an `app.state` accessor). Best-effort (try/except + log).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_realtime_hitl_publish.py -q`
Then re-run `uv run pytest tests/integration/test_hitl_takeover.py tests/integration/test_hitl_send.py -q` (no regression).
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/api/routes/console_inbox.py tests/integration/test_realtime_hitl_publish.py
git commit -m "feat(chat-rt): publish talk.updated/message.created on HITL actions"
```

---

### Task 6: Worker publishes the AI reply (`message.created` from `_run_v2_inbox`)

**Files:**
- Modify: `src/ai_sdr/worker/jobs/inbound.py` (after the turn persists the AI outbound, publish)
- Modify: `src/ai_sdr/worker/main.py` (worker holds a redis client for publishing — on `app.state`/ctx)
- Test: `tests/integration/test_realtime_worker_publish.py`

**Interfaces:**
- Consumes: `publish_inbox_event`/`publish_message_created` (T1/T4). The arq worker process needs a `redis.asyncio.Redis` (create it in the worker `on_startup` and store on the arq `ctx`, or create one per publish — prefer a shared one on `ctx["redis"]`).
- Produces: after `_run_v2_inbox` runs a turn that SENT an AI reply (outcome `"sent"`), publish `message.created` (+ `contact.updated`) on the lead's instance. Skip when the turn was `skipped_human`/`opt_out` (no message to surface, though a `talk.updated` for opt_out is fine).

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_realtime_worker_publish.py
"""After the worker turn sends an AI reply, a message.created is published.

Mirror the run_turn harness in test_turn_voice_e2e.py / test_run_turn_human_gate.py
to drive a turn whose outcome is 'sent', with a redis subscriber on the lead's
instance channel asserting a message.created arrives."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_ai_reply_publishes_message_created(run_turn_publish_harness):
    # harness: ai-mode talk, stub llm returns a normal TurnDecision (outcome 'sent'),
    # a redis subscriber on the lead's instance channel.
    events = await run_turn_publish_harness()
    assert any(e["type"] == "message.created" for e in events)
```

> Build `run_turn_publish_harness` by extending the existing `run_turn` harness (from Task 5 of Plano 2A / `test_turn_voice_e2e.py`): seed an `Instance(channel_label='main')` + the lead on 'main', subscribe a redis client to that instance channel, run `run_turn` with a stub LLM that yields a `TurnDecision` (so outcome is `'sent'`) + a FakeMessagingAdapter, then publish from the worker path and collect the events. If wiring the publish through the real worker entrypoint is heavy, the harness may call the same publish helper the worker calls right after `run_turn` returns `'sent'` — but the production wiring (the `inbound.py` change) is what's under test, so prefer driving `process_lead_inbox` end-to-end if feasible; otherwise assert the helper is invoked from the worker code path.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_realtime_worker_publish.py -q`
Expected: FAIL — no event.

- [ ] **Step 3: Wire the worker publish**

In `worker/jobs/inbound.py` `_run_v2_inbox`, after a `run_turn` that returns outcome `"sent"`, call `publish_message_created(redis, db, tenant_id=tenant.id, lead=lead, body_preview=result.response_text[:120])` using a redis client from the arq `ctx` (create it in `worker/main.py` `on_startup`: `ctx["redis"] = aioredis.from_url(get_settings().redis_url, decode_responses=True)`; close in `on_shutdown`). Best-effort.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_realtime_worker_publish.py -q`
Then re-run `uv run pytest tests/integration/test_pipeline_smoke_3_turns.py -q` (turn still works).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/worker/jobs/inbound.py src/ai_sdr/worker/main.py tests/integration/test_realtime_worker_publish.py
git commit -m "feat(chat-rt): worker publishes message.created after AI reply"
```

---

### Task 7: End-to-end realtime + full suite green

**Files:**
- Test: `tests/integration/test_realtime_e2e.py`

**Interfaces:** none (verification + one e2e).

- [ ] **Step 1: Write the e2e test**

```python
# tests/integration/test_realtime_e2e.py
"""Operator WS connected → an operator send on the same instance → the WS
client receives the message.created live. Uses the sync TestClient + the
ws_authed_ctx fixture + the authed REST client for the send."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_operator_send_pushes_live_to_ws(ws_authed_ctx):
    client, ctx = ws_authed_ctx
    inst = ctx["instance_id"]
    with client.websocket_connect(f"/ws/instances/{inst}", cookies={"pesdr_session": ctx["cookie"]}) as ws:
        # takeover + send via the REST client (ctx provides a helper or reuse the sync client)
        ctx["takeover_and_send"](text="oi do operador")
        # the send publishes message.created → the WS client should get it
        seen = [ws.receive_json() for _ in range(2)]
        assert any(e["type"] == "message.created" for e in seen)
```

> `ws_authed_ctx` must expose `takeover_and_send` (POST takeover then POST send on the seeded human talk) via the sync TestClient against `/api/console/...`. The send's best-effort publish reaches the WS through the same app `app.state.redis`/`inbox_hub`.

- [ ] **Step 2: Run the e2e + the full realtime suite**

Run:
```
uv run pytest tests/integration/test_realtime_publish.py tests/integration/test_inbox_hub.py \
  tests/integration/test_ws_inbox_route.py tests/integration/test_realtime_inbound_publish.py \
  tests/integration/test_realtime_hitl_publish.py tests/integration/test_realtime_worker_publish.py \
  tests/integration/test_realtime_e2e.py -q
```
Expected: all PASS.

- [ ] **Step 3: Regression — HITL + inbox + turn still green**

Run: `uv run pytest tests/integration/test_hitl_takeover.py tests/integration/test_hitl_send.py tests/integration/test_console_inbox_routes.py tests/integration/test_pipeline_smoke_3_turns.py -q`
Expected: PASS.

- [ ] **Step 4: Commit (if test-only fixups were needed)**

```bash
git add tests/integration/conftest.py
git commit -m "test(chat-rt): realtime e2e green"
```

---

## Self-Review

**Spec coverage (spec §8 realtime → task):**
- WS hub + Redis pub/sub → Tasks 2,3. Publish helper + `seq` (Redis INCR) → Task 1. Channel `inst:{id}` → Tasks 1-3. ✓
- Publish from the persistence points (webhook inbound, operator send, takeover/release, worker AI reply) → Tasks 4,5,6. ✓
- Event types `message.created`, `talk.updated`, `contact.updated` → Tasks 1,4,5,6. ✓
- WS auth (cookie + tenant access) → Task 3. ✓
- Backpressure (bounded queue, drop on full) → Tasks 2,3. ✓
- Reconnect: documented v1 simplification (client REST refetch + id/seq dedup; no server seq-replay — events table deferred). The `last_seq` param is accepted but not a replay cursor. ✓ (explicit constraint)

**Deferred to Plano 2B-ii (NOT here):** `message.status_updated` + delivery-status (`statuses` webhook, ✓✓); `talk.window_expired`; `whatsapp_templates` registry + closed-window template send. Deferred to a later plan: a persisted events table for true server-side seq-replay reconnect.

**Placeholder scan:** No "TBD". Several tasks point at existing harnesses to mirror (`test_console_leads_page.py` for cookie auth; `test_turn_voice_e2e.py`/`test_run_turn_human_gate.py` for the run_turn harness; the Plano-2A `authed_inbox_client_with_fake_adapter`). The sync/async bridge in the WS tests (Starlette `TestClient` is sync; publish is async) is called out as an explicit implementer decision in Task 3.

**Type consistency:** envelope shape `{seq,type,instance_id,lead_id,payload}` identical across Tasks 1-6. `publish_inbox_event(redis, *, instance_id, type, lead_id, payload) -> int` and `resolve_instance_id(session, *, tenant_id, channel_label)` / `publish_message_created(redis, session, *, tenant_id, lead, body_preview)` consistent across Tasks 1,4,5,6. `InboxHub.register/unregister/start/stop` + `Connection.offer(env)->bool` consistent across Tasks 2,3.

## Open items the implementer resolves against live code
1. **Sync/async bridge for WS tests** (Task 3): Starlette `TestClient.websocket_connect` is sync and runs the app's lifespan in a portal thread; drive the async `publish` either via `client.portal.call(...)` or a synchronous redis publish in the test. Confirm the app's lifespan actually runs under `TestClient` (it does for Starlette `TestClient` used as a context manager).
2. The `ws_authed_ctx` + `run_turn_publish_harness` fixtures — build in `tests/integration/conftest.py`, mirroring `test_console_leads_page.py` (cookie/console-enabled tenant) and `test_turn_voice_e2e.py` (run_turn harness), and ensure an `Instance(channel_label='main')` + the lead on `'main'` are seeded so `resolve_instance_id` returns a row.
3. The arq worker redis client lifecycle (Task 6): `on_startup`/`on_shutdown` in `worker/main.py` (confirm the `WorkerSettings` hooks exist).
4. Pattern-subscribe message shape: `redis.asyncio` `pubsub` `pmessage` has `message["channel"]` + `message["data"]`; confirm `decode_responses=True` gives `str` (not bytes) so `json.loads` works directly.
