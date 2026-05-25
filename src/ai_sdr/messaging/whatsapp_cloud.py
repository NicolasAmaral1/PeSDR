"""WhatsAppCloudAPIAdapter — default standalone messaging impl.

Implements the WhatsApp Cloud API surface PeSDR needs for Plano 5:
  - GET webhook verification (hub.mode=subscribe handshake)
  - POST webhook ingestion: HMAC verify + parse text messages
  - send_text via Graph API with bounded retry + typed error classification

Configuration comes from MessagingConfig; secrets are resolved by the
factory before construction (see `_build_whatsapp_cloud` below).
"""

from __future__ import annotations

from typing import Mapping

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
                f"WhatsAppCloudAPIAdapter requires provider='whatsapp_cloud' "
                f"(got {cfg.provider!r})"
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
        raise NotImplementedError("Lands in Plano 5 Task 14")

    async def send_text(self, to: str, text: str) -> SendResult:
        raise NotImplementedError("Lands in Plano 5 Task 15")


# Replace the placeholder builder registered in Task 12.
# We re-register here; the factory's _REGISTRY mutates.
from ai_sdr.messaging import factory as _factory_module  # noqa: E402

_factory_module._REGISTRY.pop("whatsapp_cloud", None)


@register_provider("whatsapp_cloud")
def _build_whatsapp_cloud(
    cfg: MessagingConfig, secrets: Mapping[str, str]
) -> MessagingAdapter:
    return WhatsAppCloudAPIAdapter(cfg, secrets)
