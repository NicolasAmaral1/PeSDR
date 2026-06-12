"""In-memory MessagingAdapter for tests and the simulate CLI.

Supports scripting: queue inbound messages, force failures on next send,
inspect what was sent. No I/O, no provider integration.
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime

from ai_sdr.messaging.base import InboundMessage, MessagingAdapter, SendResult
from ai_sdr.messaging.errors import TerminalError


class FakeMessagingAdapter(MessagingAdapter):
    """Test/dev adapter. Not for production use."""

    def __init__(self) -> None:
        self._inbound_queue: deque[InboundMessage] = deque()
        self._pending_failure: TerminalError | None = None
        self._pending_template_failure: TerminalError | None = None
        self.sent_messages: list[dict[str, str]] = []
        self.sent_templates: list[tuple[str, str, str, list[str]]] = []
        self.typing_calls: list[str] = []

    # --- scripting hooks --------------------------------------------------

    def queue_inbound(self, msg: InboundMessage) -> None:
        """Make the next handle_inbound() return this (along with any other
        previously queued messages). Each call to handle_inbound() drains
        the entire queue."""
        self._inbound_queue.append(msg)

    def fail_next_send(self, exc: TerminalError) -> None:
        """Make the next (single) send_text() raise this. Subsequent sends
        succeed normally."""
        self._pending_failure = exc

    def fail_next_template_send(self, exc: TerminalError) -> None:
        """Make the next (single) send_template() raise this. Subsequent
        template sends succeed normally."""
        self._pending_template_failure = exc

    # --- adapter interface ------------------------------------------------

    async def handle_inbound(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        out = list(self._inbound_queue)
        self._inbound_queue.clear()
        return out

    async def send_text(self, to: str, text: str) -> SendResult:
        if self._pending_failure is not None:
            exc = self._pending_failure
            self._pending_failure = None
            raise exc
        self.sent_messages.append({"to": to, "text": text})
        return SendResult(
            external_id=f"fake_{uuid.uuid4().hex[:12]}",
            sent_at_iso=datetime.now(UTC).isoformat(),
        )

    async def send_template(
        self,
        to: str,
        template_ref: str,
        language: str,
        params: list[str],
    ) -> SendResult:
        if self._pending_template_failure is not None:
            exc = self._pending_template_failure
            self._pending_template_failure = None
            raise exc
        self.sent_templates.append((to, template_ref, language, list(params)))
        return SendResult(
            external_id=f"faketmpl_{uuid.uuid4().hex[:12]}",
            sent_at_iso=datetime.now(UTC).isoformat(),
        )

    def verification_challenge(self, params: Mapping[str, str]) -> str | None:
        return params.get("hub.challenge")

    async def mark_as_typing(self, to: str) -> None:
        self.typing_calls.append(to)
