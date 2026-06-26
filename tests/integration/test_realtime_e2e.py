"""End-to-end realtime: an operator WS connected to instance X receives the
``message.created`` event live when an operator takeover+send happens on a
contact of that same instance X.

This exercises the REAL send -> publish -> WS path: the operator send POSTs to
the console HITL route, which (after commit) calls publish_message_created on
the SAME app's app.state.redis that the WS's InboxHub subscribes to.

Written as a SYNC def because the Starlette TestClient WebSocket API is sync
(it runs the app + the hub's pubsub reader in a portal thread). The takeover
and send are driven through the SAME sync TestClient, so the WS and the REST
calls share one app / one event loop / one redis bus.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


def test_operator_send_pushes_live_to_ws(ws_authed_ctx):
    client, ctx = ws_authed_ctx
    inst = ctx["instance_id"]
    with client.websocket_connect(
        f"/ws/instances/{inst}", cookies={"pesdr_session": ctx["cookie"]}
    ) as ws:
        # Operator takes over the seeded human talk and sends a message via the
        # SAME sync TestClient. The send's best-effort publish reaches the WS
        # through the shared app.state.redis / inbox_hub.
        ctx["takeover_and_send"](text="oi do operador", client_message_id=str(uuid.uuid4()))

        # The send publishes message.created (+ contact.updated). Poll a few
        # frames until we see the operator's outgoing message.created.
        seen_types = []
        for _ in range(5):
            env = ws.receive_json()
            seen_types.append(env["type"])
            if env["type"] == "message.created":
                assert env["payload"]["lead_id"] == str(ctx["lead_id"])
                break
        else:
            raise AssertionError(
                f"no message.created received over WS; saw {seen_types!r}"
            )
