"""SandboxMessagingAdapter — messaging adapter used by the Sandbox console.

Per Nicolas's PR #26 review: the sandbox MUST run the same `run_turn`
pipeline as production. The only legal difference is the adapter at the
edges — in sandbox, sends do not hit Meta; they return a fake SendResult
so the FlowEngine v2 pipeline can persist the OutboundMessage audit row
the same way it does in production. The Sandbox HTMX poll reads from
that audit row.

Contract differences vs FakeMessagingAdapter:
  - No `_inbound_queue` scripting hooks. Inbounds in sandbox come from the
    DB (operator clicks "Send" → `SandboxService.record_operator_inbound`
    writes `inbound_messages` → worker drains). `handle_inbound` here is
    a defensive no-op.
  - `verification_challenge` returns None — sandbox has no webhook handshake.

This adapter is *only* instantiated by `process_sandbox_turn` when
`talk.is_sandbox=True`. It is NOT registered in the messaging factory
(`@register_provider`) because no `tenant.yaml` should select it as a
provider — its lifetime is tied to the sandbox worker invocation.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from ai_sdr.messaging.base import InboundMessage, MessagingAdapter, SendResult


class SandboxMessagingAdapter(MessagingAdapter):
    """Adapter for sandbox Talks — fake sends, no I/O.

    Returns SendResult with provider-prefixed external_id so audit rows
    are easy to distinguish from production traffic.
    """

    async def handle_inbound(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        return []

    async def send_text(self, to: str, text: str) -> SendResult:
        return SendResult(
            external_id=f"sandbox_{uuid.uuid4().hex[:12]}",
            sent_at_iso=datetime.now(UTC).isoformat(),
        )

    async def send_template(
        self,
        to: str,
        template_ref: str,
        language: str,
        params: list[str],
    ) -> SendResult:
        return SendResult(
            external_id=f"sandbox_tmpl_{uuid.uuid4().hex[:12]}",
            sent_at_iso=datetime.now(UTC).isoformat(),
        )

    async def send_audio(
        self, to: str, audio: bytes, content_type: str
    ) -> SendResult:
        return SendResult(
            external_id=f"sandbox_audio_{uuid.uuid4().hex[:12]}",
            sent_at_iso=datetime.now(UTC).isoformat(),
        )

    async def download_media(self, media_ref: str) -> tuple[bytes, str]:
        # Sandbox never produces inbound media — return empty bytes if asked.
        return (b"", "application/octet-stream")

    def verification_challenge(self, params: Mapping[str, str]) -> str | None:
        return None

    async def mark_as_typing(self, to: str) -> None:
        # No-op — UX hint that real adapters surface to recipients. Sandbox
        # operator UI fakes the "digitando…" indicator client-side via HTMX.
        return None
