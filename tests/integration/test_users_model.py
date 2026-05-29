"""User ORM — case-insensitive unique username, no RLS."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from ai_sdr.models.user import User

pytestmark = pytest.mark.integration


async def test_create_user_with_required_fields(db_session) -> None:
    u = User(
        username=f"alice_{uuid.uuid4().hex[:6]}",
        password_hash="$2b$12$abcdefghijklmnopqrstuv.wxyz0123456789ABCDEFGHIJKLM",
    )
    db_session.add(u)
    await db_session.commit()
    assert isinstance(u.id, uuid.UUID)
    assert u.is_platform_admin is False
    assert u.created_at is not None
    assert u.last_login_at is None


async def test_username_unique_case_insensitive(db_session) -> None:
    base = f"bob_{uuid.uuid4().hex[:6]}"
    db_session.add(User(username=base, password_hash="x" * 60))
    await db_session.commit()

    db_session.add(User(username=base.upper(), password_hash="y" * 60))
    with pytest.raises(Exception):  # noqa: B017  # IntegrityError from unique index on lower(username)
        await db_session.commit()
    await db_session.rollback()


async def test_users_table_has_no_rls(db_session) -> None:
    """Sanity check: users is GLOBAL — no tenant context required to read."""
    from sqlalchemy import text

    # Reset any leftover tenant context to empty (mimics a fresh session pre-auth).
    await db_session.execute(text("SELECT set_config('app.current_tenant', '', true)"))
    rows = (await db_session.execute(select(User))).scalars().all()
    # No exception means RLS isn't blocking; we don't care about row count here.
    assert isinstance(rows, list)
