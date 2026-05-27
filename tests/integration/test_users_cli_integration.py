"""ai-sdr users CLI — exercise add/grant/revoke/passwd/list/set-admin against real DB."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from ai_sdr.cli.app import app
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess

pytestmark = pytest.mark.integration

runner = CliRunner()


async def _make_tenant(db_session) -> Tenant:
    t = Tenant(slug=f"u_{uuid.uuid4().hex[:6]}", display_name="U")
    db_session.add(t)
    await db_session.commit()
    return t


async def test_add_then_grant_then_revoke(db_session) -> None:
    tenant = await _make_tenant(db_session)
    uname = f"cli_{uuid.uuid4().hex[:6]}"

    r1 = runner.invoke(app, ["users", "add", "--username", uname, "--password", "secret123"])
    assert r1.exit_code == 0, r1.output

    user = (await db_session.execute(select(User).where(User.username == uname))).scalar_one()
    assert user.is_platform_admin is False

    r2 = runner.invoke(
        app, ["users", "grant", "--username", uname, "--tenant", tenant.slug, "--role", "operator"]
    )
    assert r2.exit_code == 0, r2.output

    grant = (
        await db_session.execute(
            select(UserTenantAccess).where(UserTenantAccess.user_id == user.id)
        )
    ).scalar_one()
    assert grant.role == "operator"

    r3 = runner.invoke(app, ["users", "revoke", "--username", uname, "--tenant", tenant.slug])
    assert r3.exit_code == 0, r3.output

    grants_left = (
        (
            await db_session.execute(
                select(UserTenantAccess).where(UserTenantAccess.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    assert grants_left == []


async def test_set_admin_toggles(db_session) -> None:
    uname = f"adm_{uuid.uuid4().hex[:6]}"
    r1 = runner.invoke(app, ["users", "add", "--username", uname, "--password", "x"])
    assert r1.exit_code == 0

    r2 = runner.invoke(app, ["users", "set-admin", "--username", uname, "--admin"])
    assert r2.exit_code == 0

    user = (await db_session.execute(select(User).where(User.username == uname))).scalar_one()
    assert user.is_platform_admin is True


async def test_add_rejects_duplicate_username_case_insensitive(db_session) -> None:
    uname = f"dup_{uuid.uuid4().hex[:6]}"
    runner.invoke(app, ["users", "add", "--username", uname, "--password", "x"])
    r2 = runner.invoke(app, ["users", "add", "--username", uname.upper(), "--password", "y"])
    assert r2.exit_code == 1
    assert "already exists" in r2.output


async def test_list_default_and_filtered(db_session) -> None:
    tenant = await _make_tenant(db_session)
    uname = f"ls_{uuid.uuid4().hex[:6]}"
    runner.invoke(app, ["users", "add", "--username", uname, "--password", "x"])
    runner.invoke(
        app,
        ["users", "grant", "--username", uname, "--tenant", tenant.slug, "--role", "operator"],
    )

    r_all = runner.invoke(app, ["users", "list"])
    assert r_all.exit_code == 0
    assert uname in r_all.output

    r_filtered = runner.invoke(app, ["users", "list", "--tenant", tenant.slug])
    assert r_filtered.exit_code == 0
    assert uname in r_filtered.output
