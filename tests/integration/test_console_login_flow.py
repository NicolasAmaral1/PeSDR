"""Console login flow — GET form, POST credential, cookie issued, logout clears."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess
from ai_sdr.web.passwords import hash_password

pytestmark = pytest.mark.integration


@pytest.fixture
async def seeded(db_session) -> tuple[User, Tenant]:
    """Insert a tenant + a user with operator grant. Returns both."""
    tenant = Tenant(slug=f"flow_{uuid.uuid4().hex[:6]}", display_name="Flow")
    db_session.add(tenant)
    await db_session.flush()
    user = User(
        username=f"u_{uuid.uuid4().hex[:6]}",
        password_hash=hash_password("correctpassword"),
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        UserTenantAccess(user_id=user.id, tenant_id=tenant.id, role="operator")
    )
    await db_session.commit()
    return user, tenant


async def test_get_login_renders_form(app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get("/console/login")
    assert r.status_code == 200
    assert "<form" in r.text
    assert 'name="username"' in r.text
    assert 'name="password"' in r.text


async def test_post_login_wrong_password_returns_401(app, seeded) -> None:
    user, _tenant = seeded
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/console/login",
            data={"username": user.username, "password": "wrong"},
        )
    assert r.status_code == 401
    assert "Usuário ou senha incorretos" in r.text
    assert "pesdr_session" not in (r.cookies or {})


async def test_post_login_unknown_user_returns_401(app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/console/login",
            data={"username": "ghost", "password": "whatever"},
        )
    assert r.status_code == 401
    # Same message as wrong-password — uniform error path
    assert "Usuário ou senha incorretos" in r.text


async def test_post_login_success_issues_cookie_and_redirects(
    app, seeded, monkeypatch
) -> None:
    user, tenant = seeded
    # Ensure CONSOLE_SECRET_KEY is set so the cookie signer works
    from ai_sdr.settings import get_settings
    monkeypatch.setattr(get_settings(), "console_secret_key", "x" * 48)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        r = await client.post(
            "/console/login",
            data={"username": user.username, "password": "correctpassword"},
        )
    assert r.status_code == 303
    assert r.headers["location"] == f"/console/{tenant.slug}/leads"
    assert "pesdr_session" in r.cookies


async def test_post_login_admin_redirects_to_a_tenant(
    app, db_session, monkeypatch
) -> None:
    from ai_sdr.settings import get_settings
    monkeypatch.setattr(get_settings(), "console_secret_key", "x" * 48)

    tenant = Tenant(slug=f"adm_{uuid.uuid4().hex[:6]}", display_name="A")
    db_session.add(tenant)
    await db_session.flush()
    admin = User(
        username=f"admin_{uuid.uuid4().hex[:6]}",
        password_hash=hash_password("adminpass"),
        is_platform_admin=True,
    )
    db_session.add(admin)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        r = await client.post(
            "/console/login",
            data={"username": admin.username, "password": "adminpass"},
        )
    assert r.status_code == 303
    # Admin lands on some tenant — at least one exists in DB after this fixture.
    assert r.headers["location"].startswith("/console/")
    assert r.headers["location"].endswith("/leads")


async def test_logout_clears_cookie_and_redirects(app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        # Send a cookie just so the deletion is meaningful at the wire level
        r = await client.get(
            "/console/logout",
            cookies={"pesdr_session": "stale"},
        )
    assert r.status_code == 303
    assert r.headers["location"] == "/console/login"
    # Either explicit Set-Cookie clearing OR cookie absent in jar
    set_cookie = r.headers.get("set-cookie", "")
    assert "pesdr_session=" in set_cookie or r.cookies.get("pesdr_session") is None
