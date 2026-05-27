"""Console auth — cookie signing + (later) FastAPI deps.

This module is the auth boundary of the console. Two responsibilities:

1. **Cookie signing** (this task): sign + verify session cookies with
   `itsdangerous.URLSafeTimedSerializer`. The cookie payload is a tiny
   dict {"user_id": "<uuid-str>"}; expiration is enforced by `max_age`
   on verify (caller passes the configured window).

2. **FastAPI deps** (Task 13): `require_console_user`, `require_tenant_access`.

The serializer is constructed lazily per call (not cached) so test
monkeypatching of settings.console_secret_key takes effect. Production
overhead is negligible — URLSafeTimedSerializer instantiation is cheap.
"""

from __future__ import annotations

import uuid

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ai_sdr.settings import get_settings

_SALT = "pesdr-console-v1"


def _serializer() -> URLSafeTimedSerializer:
    secret = get_settings().console_secret_key
    if not secret or len(secret) < 32:
        raise RuntimeError(
            "CONSOLE_SECRET_KEY must be set (32+ chars). Startup validator "
            "should have caught this — check main.py lifespan."
        )
    return URLSafeTimedSerializer(secret, salt=_SALT)


def sign_session_cookie(user_id: uuid.UUID) -> str:
    """Return a signed cookie value carrying `user_id`."""
    return _serializer().dumps({"user_id": str(user_id)})


def verify_session_cookie(cookie_value: str, *, max_age_seconds: int) -> dict[str, str] | None:
    """Return the payload if signature is valid and not expired; else None.

    None covers: signature mismatch, expired, malformed input, empty
    string. Caller treats every None case as "log them out".
    """
    if not cookie_value:
        return None
    try:
        return _serializer().loads(cookie_value, max_age=max_age_seconds)  # type: ignore[no-any-return]
    except (BadSignature, SignatureExpired):
        return None
