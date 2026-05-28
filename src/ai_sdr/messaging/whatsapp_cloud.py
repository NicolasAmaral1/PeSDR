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
from datetime import datetime

import httpx
import structlog
import tenacity

from ai_sdr.messaging.base import (
    InboundMessage,
    MessagingAdapter,
    SendResult,
)
from ai_sdr.messaging.errors import (
    AuthError,
    PolicyError,
    RateLimitError,
    RecipientUnreachable,
    SignatureError,
    TransientError,
    WindowExpiredError,
)
from ai_sdr.messaging.factory import register_provider
from ai_sdr.schemas.tenant_yaml import MessagingConfig

log = structlog.get_logger(__name__)

# Tenacity wait strategy is exposed at module level so tests can monkeypatch
# it to zero. Production wait is exponential 1s, 2s, 4s.
_WAIT_STRATEGY = tenacity.wait_exponential(multiplier=1, min=1, max=4)
_MAX_ATTEMPTS = 3


def _build_http_client() -> httpx.AsyncClient:
    """Factory hook — tests patch this to inject a mock transport."""
    return httpx.AsyncClient(timeout=15.0)


def _classify_error(
    status: int, error: dict[str, object] | None, retry_after_s: int | None
) -> Exception:
    """Map a (status, error body) pair to one of our typed exceptions."""
    code = (error or {}).get("code")

    if status in (401, 403) or code == 190:
        return AuthError(f"WhatsApp auth error: {error!r}")
    if status == 400:
        if code == 131026 or code == 131051:
            return RecipientUnreachable(f"recipient unreachable: {error!r}")
        if code == 131047:
            return WindowExpiredError(f"24h window expired: {error!r}")
        if code in (131048, 131049):
            return PolicyError(f"policy violation: {error!r}")
        # Conservative catch-all for unknown 4xx — alert ops.
        return PolicyError(f"unknown 4xx: status={status} body={error!r}")
    if status == 429:
        return RateLimitError(retry_after_s=retry_after_s or 60)
    if 500 <= status < 600:
        return TransientError(f"5xx from WhatsApp: status={status} body={error!r}")
    return TransientError(f"unexpected status {status}: {error!r}")


class WhatsAppCloudAPIAdapter(MessagingAdapter):
    """Production-grade adapter for WhatsApp Cloud API (Meta Graph)."""

    def __init__(self, cfg: MessagingConfig, secrets: Mapping[str, str]) -> None:
        if cfg.provider != "whatsapp_cloud":
            raise ValueError(
                f"WhatsAppCloudAPIAdapter requires provider='whatsapp_cloud' (got {cfg.provider!r})"
            )
        # MessagingConfig._check_provider_fields guarantees these are non-None
        # when provider='whatsapp_cloud' — assert for the type checker.
        assert cfg.phone_number_id_ref is not None
        assert cfg.access_token_ref is not None
        assert cfg.webhook_verify_token_ref is not None
        assert cfg.app_secret_ref is not None
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
        url = f"https://graph.facebook.com/{self._api_version}/{self._phone_number_id}/messages"
        body = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "text",
            "text": {"body": text},
        }
        request_headers = {"Authorization": f"Bearer {self._access_token}"}

        retryer = tenacity.AsyncRetrying(
            stop=tenacity.stop_after_attempt(_MAX_ATTEMPTS),
            wait=_WAIT_STRATEGY,
            retry=tenacity.retry_if_exception_type(TransientError),
            reraise=True,
        )

        log.info("wa.send.start", to=to, attempts_max=_MAX_ATTEMPTS)
        async for attempt in retryer:
            with attempt:
                async with _build_http_client() as client:
                    response = await client.post(url, json=body, headers=request_headers)
                if response.status_code == 200:
                    data = response.json()
                    out_id = data["messages"][0]["id"]
                    log.info(
                        "wa.send.success",
                        to=to,
                        external_id=out_id,
                        attempt=attempt.retry_state.attempt_number,
                    )
                    return SendResult(
                        external_id=out_id,
                        sent_at_iso=datetime.now(dt.UTC).isoformat(),
                    )
                # Non-200: classify, raise (tenacity decides retry vs terminal).
                try:
                    err_body = response.json().get("error")
                except Exception:
                    err_body = None
                retry_after_hdr = response.headers.get("Retry-After")
                retry_after_s = int(retry_after_hdr) if retry_after_hdr else None
                exc = _classify_error(response.status_code, err_body, retry_after_s)
                log.warning(
                    "wa.send.error",
                    to=to,
                    status=response.status_code,
                    err_type=type(exc).__name__,
                    err=str(exc),
                    attempt=attempt.retry_state.attempt_number,
                )
                raise exc

        raise RuntimeError("unreachable: tenacity exhausted without raising")

    async def send_template(
        self,
        to: str,
        template_ref: str,
        language: str,
        params: list[str],
    ) -> SendResult:
        url = f"https://graph.facebook.com/{self._api_version}/{self._phone_number_id}/messages"
        template_block: dict[str, object] = {
            "name": template_ref,
            "language": {"code": language},
        }
        if params:
            template_block["components"] = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in params],
                }
            ]
        body = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "template",
            "template": template_block,
        }
        request_headers = {"Authorization": f"Bearer {self._access_token}"}

        retryer = tenacity.AsyncRetrying(
            stop=tenacity.stop_after_attempt(_MAX_ATTEMPTS),
            wait=_WAIT_STRATEGY,
            retry=tenacity.retry_if_exception_type(TransientError),
            reraise=True,
        )

        log.info("wa.send_template.start", to=to, template_ref=template_ref)
        async for attempt in retryer:
            with attempt:
                async with _build_http_client() as client:
                    response = await client.post(url, json=body, headers=request_headers)
                if response.status_code == 200:
                    data = response.json()
                    out_id = data["messages"][0]["id"]
                    log.info(
                        "wa.send_template.success",
                        to=to,
                        template_ref=template_ref,
                        external_id=out_id,
                        attempt=attempt.retry_state.attempt_number,
                    )
                    return SendResult(
                        external_id=out_id,
                        sent_at_iso=datetime.now(dt.UTC).isoformat(),
                    )
                try:
                    err_body = response.json().get("error")
                except Exception:
                    err_body = None
                retry_after_hdr = response.headers.get("Retry-After")
                retry_after_s = int(retry_after_hdr) if retry_after_hdr else None
                exc = _classify_error(response.status_code, err_body, retry_after_s)
                log.warning(
                    "wa.send_template.error",
                    to=to,
                    template_ref=template_ref,
                    status=response.status_code,
                    err_type=type(exc).__name__,
                    err=str(exc),
                )
                raise exc

        raise RuntimeError("unreachable: tenacity exhausted without raising")


# Replace the placeholder builder registered in Task 12.
# We re-register here; the factory's _REGISTRY mutates.
from ai_sdr.messaging import factory as _factory_module  # noqa: E402

_factory_module._REGISTRY.pop("whatsapp_cloud", None)


@register_provider("whatsapp_cloud")
def _build_whatsapp_cloud(cfg: MessagingConfig, secrets: Mapping[str, str]) -> MessagingAdapter:
    return WhatsAppCloudAPIAdapter(cfg, secrets)
