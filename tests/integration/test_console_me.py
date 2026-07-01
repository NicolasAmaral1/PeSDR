"""GET /api/console/me returns the authed user + their accessible tenants.
Mirrors the cookie-auth seeding of tests/integration/test_console_leads_page.py."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_me_returns_user_and_tenants(authed_inbox_client):
    client, ctx = authed_inbox_client
    resp = await client.get("/api/console/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["username"] == ctx["user"].username
    slugs = [t["slug"] for t in body["tenants"]]
    assert ctx["slug"] in slugs


async def test_me_unauthenticated_redirects(app):
    from httpx import ASGITransport, AsyncClient

    # require_console_user REDIRECTS to /console/login on missing cookie
    # (it does NOT return 401). Assert the redirect, with follow disabled.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        resp = await client.get("/api/console/me")
    assert resp.status_code in (302, 303, 307)
    assert "/console/login" in resp.headers.get("location", "")
