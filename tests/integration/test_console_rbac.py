"""Console RBAC: operator scoping, admin override, console.enabled=false → 404."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient

from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess
from ai_sdr.web.auth import require_tenant_access, sign_session_cookie
from ai_sdr.web.passwords import hash_password

pytestmark = pytest.mark.integration


def _mk_ping_router() -> APIRouter:
    r = APIRouter()

    @r.get("/console/{tenant_slug}/__ping__")
    async def ping(
        access: Annotated[tuple, Depends(require_tenant_access)],
    ):
        tenant, user = access
        return {"tenant": tenant.slug, "user": user.username, "admin": user.is_platform_admin}

    return r


def _patch_settings(monkeypatch, secret: str = "x" * 48) -> None:
    from ai_sdr.settings import get_settings

    monkeypatch.setattr(get_settings(), "console_secret_key", secret)


def _make_tenant_yaml(tmpdir: Path, slug: str, enabled: bool) -> None:
    (tmpdir / slug).mkdir(parents=True, exist_ok=True)
    yaml = f"""id: {slug}
display_name: {slug.title()}
timezone: UTC
llm:
  default:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_ref: secrets/anthropic_key
console:
  enabled: {"true" if enabled else "false"}
"""
    (tmpdir / slug / "tenant.yaml").write_text(yaml)


@pytest.fixture
def isolated_tenants_dir(monkeypatch):
    """Create a temp tenants/ directory + point settings at it for this test."""
    with tempfile.TemporaryDirectory() as td:
        from ai_sdr.settings import get_settings

        monkeypatch.setattr(get_settings(), "tenants_dir", td)
        yield Path(td)


@pytest.fixture
async def seeded(db_session, isolated_tenants_dir) -> dict:
    """Create: tenant_a (enabled), tenant_b (enabled), tenant_c (disabled),
    operator with grant to tenant_a, admin (no grants)."""
    tenant_a = Tenant(slug=f"a-{uuid.uuid4().hex[:6]}", display_name="A")
    tenant_b = Tenant(slug=f"b-{uuid.uuid4().hex[:6]}", display_name="B")
    tenant_c = Tenant(slug=f"c-{uuid.uuid4().hex[:6]}", display_name="C")
    db_session.add_all([tenant_a, tenant_b, tenant_c])
    await db_session.flush()

    _make_tenant_yaml(isolated_tenants_dir, tenant_a.slug, enabled=True)
    _make_tenant_yaml(isolated_tenants_dir, tenant_b.slug, enabled=True)
    _make_tenant_yaml(isolated_tenants_dir, tenant_c.slug, enabled=False)

    operator = User(username=f"op_{uuid.uuid4().hex[:6]}", password_hash=hash_password("p"))
    admin = User(
        username=f"adm_{uuid.uuid4().hex[:6]}",
        password_hash=hash_password("p"),
        is_platform_admin=True,
    )
    db_session.add_all([operator, admin])
    await db_session.flush()
    db_session.add(UserTenantAccess(user_id=operator.id, tenant_id=tenant_a.id, role="operator"))
    await db_session.commit()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "tenant_c": tenant_c,
        "operator": operator,
        "admin": admin,
    }


@pytest.fixture
def app_with_ping(app):
    app.include_router(_mk_ping_router())
    return app


async def test_operator_can_access_granted_tenant(app_with_ping, seeded, monkeypatch) -> None:
    _patch_settings(monkeypatch)
    cookie = sign_session_cookie(seeded["operator"].id)
    async with AsyncClient(
        transport=ASGITransport(app=app_with_ping), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/console/{seeded['tenant_a'].slug}/__ping__",
            cookies={"pesdr_session": cookie},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["tenant"] == seeded["tenant_a"].slug
    assert body["admin"] is False


async def test_operator_without_grant_gets_403(app_with_ping, seeded, monkeypatch) -> None:
    _patch_settings(monkeypatch)
    cookie = sign_session_cookie(seeded["operator"].id)
    async with AsyncClient(
        transport=ASGITransport(app=app_with_ping), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/console/{seeded['tenant_b'].slug}/__ping__",
            cookies={"pesdr_session": cookie},
        )
    assert r.status_code == 403


async def test_admin_accesses_any_tenant(app_with_ping, seeded, monkeypatch) -> None:
    _patch_settings(monkeypatch)
    cookie = sign_session_cookie(seeded["admin"].id)
    async with AsyncClient(
        transport=ASGITransport(app=app_with_ping), base_url="http://test"
    ) as client:
        r1 = await client.get(
            f"/console/{seeded['tenant_a'].slug}/__ping__",
            cookies={"pesdr_session": cookie},
        )
        r2 = await client.get(
            f"/console/{seeded['tenant_b'].slug}/__ping__",
            cookies={"pesdr_session": cookie},
        )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["admin"] is True


async def test_disabled_console_returns_404_even_for_admin(
    app_with_ping, seeded, monkeypatch
) -> None:
    _patch_settings(monkeypatch)
    cookie = sign_session_cookie(seeded["admin"].id)
    async with AsyncClient(
        transport=ASGITransport(app=app_with_ping), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/console/{seeded['tenant_c'].slug}/__ping__",
            cookies={"pesdr_session": cookie},
        )
    assert r.status_code == 404


async def test_no_cookie_redirects_to_login(app_with_ping, seeded) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_with_ping),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        r = await client.get(f"/console/{seeded['tenant_a'].slug}/__ping__")
    assert r.status_code == 303
    assert r.headers["location"] == "/console/login"
