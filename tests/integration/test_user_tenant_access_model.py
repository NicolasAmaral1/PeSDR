"""UserTenantAccess ORM — composite PK + FK cascades + role check constraint."""

from __future__ import annotations

import uuid

import pytest

from ai_sdr.models.tenant import Tenant
from ai_sdr.models.user import User
from ai_sdr.models.user_tenant_access import UserTenantAccess

pytestmark = pytest.mark.integration


async def _make_user(db_session) -> User:
    u = User(
        username=f"u_{uuid.uuid4().hex[:6]}",
        password_hash="$2b$12$" + "x" * 53,
    )
    db_session.add(u)
    await db_session.flush()
    return u


async def _make_tenant(db_session) -> Tenant:
    t = Tenant(slug=f"t_{uuid.uuid4().hex[:6]}", display_name="T")
    db_session.add(t)
    await db_session.flush()
    return t


async def test_grant_operator_role(db_session) -> None:
    u = await _make_user(db_session)
    t = await _make_tenant(db_session)
    grant = UserTenantAccess(user_id=u.id, tenant_id=t.id, role="operator")
    db_session.add(grant)
    await db_session.commit()
    assert grant.role == "operator"


async def test_role_check_constraint_rejects_invalid(db_session) -> None:
    u = await _make_user(db_session)
    t = await _make_tenant(db_session)
    db_session.add(UserTenantAccess(user_id=u.id, tenant_id=t.id, role="god"))
    with pytest.raises(Exception):  # noqa: B017  # ck_user_tenant_access_role violated
        await db_session.commit()
    await db_session.rollback()


async def test_user_cascade_delete_removes_grants(db_session) -> None:
    from sqlalchemy import select

    u = await _make_user(db_session)
    t = await _make_tenant(db_session)
    db_session.add(UserTenantAccess(user_id=u.id, tenant_id=t.id, role="operator"))
    await db_session.commit()

    await db_session.delete(u)
    await db_session.commit()

    rows = (
        (await db_session.execute(select(UserTenantAccess).where(UserTenantAccess.user_id == u.id)))
        .scalars()
        .all()
    )
    assert rows == []
