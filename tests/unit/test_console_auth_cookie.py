"""Cookie signer — signing roundtrip, expiration, tampering."""

from __future__ import annotations

import time
import uuid


def _patch_settings(monkeypatch, secret: str) -> None:
    """Make settings.console_secret_key return `secret` for this test."""
    from ai_sdr.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "console_secret_key", secret)


def test_sign_and_verify_roundtrip(monkeypatch) -> None:
    _patch_settings(monkeypatch, "x" * 48)
    from ai_sdr.web.auth import sign_session_cookie, verify_session_cookie

    uid = uuid.uuid4()
    cookie = sign_session_cookie(uid)
    assert isinstance(cookie, str) and len(cookie) > 20
    payload = verify_session_cookie(cookie, max_age_seconds=3600)
    assert payload is not None
    assert payload["user_id"] == str(uid)


def test_verify_rejects_tampered(monkeypatch) -> None:
    _patch_settings(monkeypatch, "x" * 48)
    from ai_sdr.web.auth import sign_session_cookie, verify_session_cookie

    cookie = sign_session_cookie(uuid.uuid4())
    tampered = cookie[:-3] + "AAA"
    assert verify_session_cookie(tampered, max_age_seconds=3600) is None


def test_verify_rejects_expired(monkeypatch) -> None:
    _patch_settings(monkeypatch, "x" * 48)
    from ai_sdr.web.auth import sign_session_cookie, verify_session_cookie

    cookie = sign_session_cookie(uuid.uuid4())
    # Sleep 2s, then verify with max_age=1
    time.sleep(2)
    assert verify_session_cookie(cookie, max_age_seconds=1) is None


def test_verify_rejects_garbage(monkeypatch) -> None:
    _patch_settings(monkeypatch, "x" * 48)
    from ai_sdr.web.auth import verify_session_cookie

    assert verify_session_cookie("not-a-real-cookie", max_age_seconds=3600) is None
    assert verify_session_cookie("", max_age_seconds=3600) is None


def test_different_secrets_invalidate(monkeypatch) -> None:
    """A cookie signed with secret A must not verify under secret B."""
    _patch_settings(monkeypatch, "a" * 48)
    from ai_sdr.web.auth import sign_session_cookie, verify_session_cookie

    cookie = sign_session_cookie(uuid.uuid4())
    _patch_settings(monkeypatch, "b" * 48)
    # Re-import: the serializer is lazy and reads settings on call.
    assert verify_session_cookie(cookie, max_age_seconds=3600) is None
