"""Exception hierarchy: every typed error inherits MessagingError;
terminal errors inherit TerminalError; RateLimitError carries retry_after_s."""

from __future__ import annotations

import pytest

from ai_sdr.messaging.errors import (
    AuthError,
    MessagingError,
    PolicyError,
    RateLimitError,
    RecipientUnreachable,
    SignatureError,
    TerminalError,
    TransientError,
    WindowExpiredError,
)


@pytest.mark.parametrize(
    "exc_type",
    [SignatureError, TerminalError, TransientError, AuthError, PolicyError,
     RecipientUnreachable, WindowExpiredError, RateLimitError],
)
def test_all_inherit_messaging_error(exc_type) -> None:
    assert issubclass(exc_type, MessagingError)


@pytest.mark.parametrize(
    "exc_type",
    [AuthError, PolicyError, RecipientUnreachable, WindowExpiredError],
)
def test_terminal_subclasses(exc_type) -> None:
    assert issubclass(exc_type, TerminalError)


def test_rate_limit_inherits_transient() -> None:
    assert issubclass(RateLimitError, TransientError)


def test_rate_limit_carries_retry_after() -> None:
    e = RateLimitError(retry_after_s=42)
    assert e.retry_after_s == 42
    assert "42" in str(e) or "retry_after" in str(e)
