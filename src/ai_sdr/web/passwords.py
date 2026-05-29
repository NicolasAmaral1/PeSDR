"""Bcrypt wrappers — thin layer so call sites don't import bcrypt directly.

Lets us swap the algorithm (e.g., to argon2) later without touching
auth.py or the users CLI.
"""

from __future__ import annotations

import bcrypt


def hash_password(plain: str) -> str:
    """Return a bcrypt hash (cost 12) of the plaintext password.

    The returned string is the standard bcrypt encoding (starts with
    $2b$12$...) — safe to store in a TEXT column.
    """
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """True iff `plain` matches `hashed`. False for any error (garbage hash,
    empty inputs, etc.) — never raises, so callers can treat all failures
    uniformly without leaking which case (timing-attack safe up to bcrypt's
    natural variance)."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
