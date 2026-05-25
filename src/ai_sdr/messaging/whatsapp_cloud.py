"""WhatsAppCloudAPIAdapter — default standalone messaging impl.

Implements the WhatsApp Cloud API surface PeSDR needs for Plano 5:
  - GET webhook verification (hub.mode=subscribe handshake)
  - POST webhook ingestion: HMAC verify + parse text messages
  - send_text via Graph API with bounded retry + typed error classification

Configuration comes from MessagingConfig; secrets are resolved by the
factory before construction (see `_build_whatsapp_cloud` below).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from collections.abc import Mapping

from ai_sdr.messaging.base import (
    InboundMessage,
    MessagingAdapter,
    SendResult,
)
from ai_sdr.messaging.errors import SignatureError
from ai_sdr.messaging.factory import register_provider
from ai_sdr.schemas.tenant_yaml import MessagingConfig


class WhatsAppCloudAPIAdapter(MessagingAdapter):
    """Production-grade adapter for WhatsApp Cloud API (Meta Graph)."""

    def __init__(self, cfg: MessagingConfig, secrets: Mapping[str, str]) -> None:
        if cfg.provider != "whatsapp_cloud":
            raise ValueError(
                f"WhatsAppCloudAPIAdapter requires provider='whatsapp_cloud' (got {cfg.provider!r})"
            )
        # The factory has already validated *_ref shape; here we just bare-
        # name lookup the resolved secrets.
        self._phone_number_id = secrets[cfg.phone_number_id_ref.removeprefix("secrets/")]
        self._access_token = secrets[cfg.access_token_ref.removeprefix("secrets/")]
        self._verify_token = secrets[cfg.webhook_verify_token_ref.removeprefix("secrets/")]
        self._app_secret = secrets[cfg.app_secret_ref.removeprefix("secrets/")]
        self._api_version = cfg.api_version

    def verification_challenge(self, params: Mapping[str, str]) -> str | None:
        """WhatsApp Cloud's GET webhook handshake.

        Returns the value of `hub.challenge` only when mode=subscribe AND
        the verify token matches what's configured. Returns None when the
        request is not a challenge at all (caller returns 404). Raises
        SignatureError when mode IS subscribe but the token is wrong
        (caller returns 401)."""
        if params.get("hub.mode") != "subscribe":
            return None
        if params.get("hub.verify_token") != self._verify_token:
            raise SignatureError("verify token mismatch")
        return params.get("hub.challenge")

    async def handle_inbound(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        # Header lookup is case-insensitive — uvicorn lowercases, but tests
        # and proxies may not, so we normalize.
        sig_header = next(
            (v for k, v in headers.items() if k.lower() == "x-hub-signature-256"),
            "",
        )
        if not sig_header.startswith("sha256="):
            raise SignatureError("missing or malformed X-Hub-Signature-256 header")
        expected = (
            "sha256=" + hmac.new(self._app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(expected, sig_header):
            raise SignatureError("HMAC mismatch")

        payload = json.loads(raw_body)
        out: list[InboundMessage] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                # Status updates have `statuses` but no `messages`.
                for m in value.get("messages", []):
                    if m.get("type") != "text":
                        continue  # Plano 5: text only. Audio/image come in Plano 8.
                    text_body = (m.get("text") or {}).get("body", "")
                    received_dt = dt.datetime.fromtimestamp(int(m["timestamp"]), tz=dt.UTC)
                    out.append(
                        InboundMessage(
                            external_id=m["id"],
                            from_address="+" + m["from"],
                            text=text_body,
                            received_at_iso=received_dt.isoformat(),
                            raw=m,
                        )
                    )
        return out

    async def send_text(self, to: str, text: str) -> SendResult:
        raise NotImplementedError("Lands in Plano 5 Task 15")


# Replace the placeholder builder registered in Task 12.
# We re-register here; the factory's _REGISTRY mutates.
from ai_sdr.messaging import factory as _factory_module  # noqa: E402

_factory_module._REGISTRY.pop("whatsapp_cloud", None)


@register_provider("whatsapp_cloud")
def _build_whatsapp_cloud(cfg: MessagingConfig, secrets: Mapping[str, str]) -> MessagingAdapter:
    return WhatsAppCloudAPIAdapter(cfg, secrets)
