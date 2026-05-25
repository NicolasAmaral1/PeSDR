"""Exception hierarchy for the messaging adapter boundary.

Terminal vs transient is the key distinction:
  - SignatureError       webhook auth failed → caller returns 401.
  - TerminalError        adapter gave up. Worker decides next action per subtype:
      - AuthError           bad provider token → log + alert ops.
      - RecipientUnreachable number not on the channel → mark lead.unreachable.
      - WindowExpiredError  outside 24h window → Plano 9 hook (template HSM).
      - PolicyError         provider policy violation → log + alert ops.
  - TransientError       adapter SHOULD retry internally; never re-raised.
      - RateLimitError      provider 429 with Retry-After header.
"""

from __future__ import annotations


class MessagingError(Exception):
    """Base for any messaging-related error."""


class SignatureError(MessagingError):
    """Webhook signature (HMAC) verification failed. Caller returns HTTP 401."""


class TerminalError(MessagingError):
    """Adapter exhausted internal retries. Worker handles per subtype."""


class AuthError(TerminalError):
    """Provider rejected the credentials (401/403/code 190 in WhatsApp)."""


class RecipientUnreachable(TerminalError):
    """The destination address cannot receive messages on this channel."""


class WindowExpiredError(TerminalError):
    """The 24h messaging window expired; only templates are allowed."""


class PolicyError(TerminalError):
    """Provider rejected the message content (spam/policy violation)."""


class TransientError(MessagingError):
    """Recoverable error; adapter retries internally with backoff."""


class RateLimitError(TransientError):
    """Provider rate-limited; adapter must respect retry_after_s."""

    def __init__(self, retry_after_s: int):
        super().__init__(f"rate limited; retry_after_s={retry_after_s}")
        self.retry_after_s = retry_after_s
