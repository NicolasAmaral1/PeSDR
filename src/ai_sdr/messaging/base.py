"""MessagingAdapter contract — the boundary between PeSDR runtime and a
messaging provider.

Adapter purity invariants:
  - Knows nothing about the `leads` or `tenants` tables.
  - Speaks opaque provider-native addresses via `to: str` (E.164 for
    WhatsApp Cloud, `vialum_contact_id` for a future Vialum adapter, etc).
  - Receives tenant-specific config + secrets at construction; never reads
    them at request time.
  - Retries Transient/RateLimit errors internally with bounded backoff;
    surfaces only TerminalError subtypes (plus SignatureError on inbound).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class InboundMessage:
    """A normalized inbound message produced by handle_inbound().

    `external_id` is the provider-native message id used for idempotent
    dedupe at the persistence layer. `from_address` is the provider-native
    sender address (E.164 for WhatsApp); the runtime resolves it to a
    lead via find_or_create_lead_by_address(). `raw` is the full original
    payload, persisted for audit.
    """

    external_id: str
    from_address: str
    text: str
    received_at_iso: str
    raw: Mapping[str, object]


@dataclass(frozen=True)
class SendResult:
    """Successful delivery — what the worker logs and persists."""

    external_id: str
    sent_at_iso: str


class MessagingAdapter(ABC):
    """Boundary between PeSDR runtime and a messaging provider."""

    @abstractmethod
    async def handle_inbound(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        """Verify signature, parse, normalize.

        Returns []:
          - for challenge/verification requests that arrive at the POST URL
            (some providers do this; WhatsApp uses GET so it's a no-op here);
          - for status updates / read receipts / typing indicators;
          - for non-text messages (Plano 5 ignores audio/image/document —
            Plano 8 will re-introduce them as MediaPart).

        Raises SignatureError if HMAC verification fails. Caller returns 401.
        """

    @abstractmethod
    async def send_text(self, to: str, text: str) -> SendResult:
        """Deliver text to recipient. Adapter retries Transient/RateLimit
        internally with bounded backoff. Raises typed terminal errors:
        AuthError, RecipientUnreachable, WindowExpiredError, PolicyError.
        """

    @abstractmethod
    async def send_template(
        self,
        to: str,
        template_ref: str,
        language: str,
        params: list[str],
    ) -> SendResult:
        """Send a pre-approved HSM template. Provider validates template_ref
        + language + params shape against its registered templates.

        Returns SendResult (same shape as send_text). Adapter retries
        Transient/RateLimit internally; raises typed terminal errors
        (AuthError, RecipientUnreachable, PolicyError) on terminal failures.

        WindowExpiredError should NEVER fire for templates — HSM messages
        bypass the 24h window. If it does, treat as adapter bug.
        """

    @abstractmethod
    def verification_challenge(self, params: Mapping[str, str]) -> str | None:
        """For providers with a GET-based webhook challenge (WhatsApp's
        hub.mode=subscribe handshake). Returns the challenge token to echo.
        """
