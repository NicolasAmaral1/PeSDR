"""Unit tests for require_console_user / require_tenant_access via mocked DB."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


def _patch_settings(monkeypatch, secret: str = "x" * 48) -> None:
    from ai_sdr.settings import get_settings

    monkeypatch.setattr(get_settings(), "console_secret_key", secret)


async def test_require_console_user_no_cookie_redirects(monkeypatch) -> None:
    _patch_settings(monkeypatch)
    from ai_sdr.web.auth import require_console_user

    request = MagicMock()
    request.cookies = {}
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await require_console_user(request=request, db=db)
    assert exc.value.status_code == 303
    assert exc.value.headers["Location"] == "/console/login"


async def test_require_console_user_bad_cookie_redirects(monkeypatch) -> None:
    _patch_settings(monkeypatch)
    from ai_sdr.web.auth import require_console_user

    request = MagicMock()
    request.cookies = {"pesdr_session": "garbage"}
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await require_console_user(request=request, db=db)
    assert exc.value.status_code == 303


async def test_require_console_user_unknown_user_redirects(monkeypatch) -> None:
    _patch_settings(monkeypatch)
    from ai_sdr.web.auth import (
        require_console_user,
        sign_session_cookie,
    )

    request = MagicMock()
    request.cookies = {"pesdr_session": sign_session_cookie(uuid.uuid4())}
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)  # user not found
    with pytest.raises(HTTPException) as exc:
        await require_console_user(request=request, db=db)
    assert exc.value.status_code == 303


async def test_require_console_user_returns_user(monkeypatch) -> None:
    _patch_settings(monkeypatch)
    from ai_sdr.models.user import User
    from ai_sdr.web.auth import require_console_user, sign_session_cookie

    user = User(id=uuid.uuid4(), username="u", password_hash="x" * 60)
    request = MagicMock()
    request.cookies = {"pesdr_session": sign_session_cookie(user.id)}
    db = AsyncMock()
    db.get = AsyncMock(return_value=user)
    out = await require_console_user(request=request, db=db)
    assert out is user
