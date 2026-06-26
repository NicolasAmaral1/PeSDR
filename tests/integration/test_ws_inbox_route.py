"""WS handshake auth + live delivery. Mirrors the cookie-auth pattern of
tests/integration/test_console_leads_page.py for seeding User+access+cookie.

Written as SYNC defs because the Starlette TestClient WebSocket API is sync
(it runs the app in a portal thread)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_ws_receives_published_event(ws_authed_ctx):
    # ws_authed_ctx: an authenticated Starlette TestClient + a seeded instance_id
    # + a redis client + the app (with lifespan run so app.state.redis/inbox_hub exist).
    client, ctx = ws_authed_ctx
    inst = ctx["instance_id"]
    with client.websocket_connect(
        f"/ws/instances/{inst}", cookies={"pesdr_session": ctx["cookie"]}
    ) as ws:
        ctx["publish"](type="talk.updated", lead_id=None, payload={"status": "requires_review"})
        data = ws.receive_json()
        assert data["type"] == "talk.updated"
        assert data["payload"]["status"] == "requires_review"


def test_ws_rejects_unauthenticated(ws_authed_ctx):
    from starlette.websockets import WebSocketDisconnect

    client, ctx = ws_authed_ctx
    inst = ctx["instance_id"]
    # close(4401) before accept surfaces as a WS connect failure.
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(f"/ws/instances/{inst}"),  # no cookie
    ):
        pass
    assert exc_info.value.code == 4401
