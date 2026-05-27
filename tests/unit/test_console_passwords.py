"""Password hashing wrappers (bcrypt)."""

from __future__ import annotations

import pytest

from ai_sdr.web.passwords import hash_password, verify_password


def test_hash_and_verify_correct_password() -> None:
    h = hash_password("correct horse battery staple")
    assert h.startswith("$2")  # bcrypt
    assert verify_password("correct horse battery staple", h) is True


def test_verify_wrong_password() -> None:
    h = hash_password("right")
    assert verify_password("wrong", h) is False


def test_verify_garbage_hash_returns_false() -> None:
    """A malformed hash must not raise — return False so timing-attack
    paths are uniform with 'wrong password'."""
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_two_hashes_of_same_password_differ() -> None:
    """bcrypt uses random salt — same plaintext yields distinct hashes."""
    a = hash_password("same")
    b = hash_password("same")
    assert a != b
    assert verify_password("same", a) is True
    assert verify_password("same", b) is True


@pytest.mark.parametrize("password", ["", " ", "x" * 72, "🚀💥", "senha com espaços"])
def test_roundtrip_edge_cases(password: str) -> None:
    h = hash_password(password)
    assert verify_password(password, h) is True
