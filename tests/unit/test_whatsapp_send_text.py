"""send_text: error classification table + happy path + retry behavior.

Uses httpx's mock transport to drive deterministic responses without a
real network. The retry logic is tested with deterministic sleep (we
patch tenacity's wait time to zero so the test runs instantly).
"""

from __future__ import annotations

import httpx
import pytest

from ai_sdr.messaging.errors import (
    AuthError,
    PolicyError,
    RateLimitError,
    RecipientUnreachable,
    TransientError,
    WindowExpiredError,
)
from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig


@pytest.fixture
def adapter_no_retry_sleep(monkeypatch) -> WhatsAppCloudAPIAdapter:
    """Adapter with retry wait patched to 0s for deterministic test runs."""
    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "PNID",
        "wa_token": "TOKEN",
        "wa_verify": "vt",
        "wa_app_secret": "as",
    }
    a = WhatsAppCloudAPIAdapter(cfg, secrets)
    # Patch the wait_strategy to zero so retries are instantaneous.
    import tenacity

    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._WAIT_STRATEGY",
        tenacity.wait_none(),
    )
    return a


def _mount(client_response: httpx.Response):
    """Return a Transport that returns the given response."""
    return httpx.MockTransport(lambda request: client_response)


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"input": "+5511999999999", "wa_id": "5511999999999"}],
            "messages": [{"id": "wamid.OUT_SENT_AAAA="}],
        },
    )


def _error_response(
    status: int,
    code: int,
    subcode: int | None = None,
    message: str = "err",
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    error: dict[str, object] = {"code": code, "message": message}
    if subcode is not None:
        error["error_subcode"] = subcode
    return httpx.Response(
        status,
        json={"error": error},
        headers=extra_headers or {},
    )


async def test_send_text_happy_path(adapter_no_retry_sleep, monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=_mount(_ok_response()), timeout=15.0),
    )
    r = await adapter_no_retry_sleep.send_text("+5511999999999", "hello")
    assert r.external_id == "wamid.OUT_SENT_AAAA="


@pytest.mark.parametrize(
    "status, code, expected_exc",
    [
        (401, 190, AuthError),
        (403, 190, AuthError),
        (400, 131026, RecipientUnreachable),
        (400, 131051, RecipientUnreachable),
        (400, 131047, WindowExpiredError),
        (400, 131048, PolicyError),
        (400, 131049, PolicyError),
        (400, 999999, PolicyError),  # unknown 4xx → conservative PolicyError
    ],
)
async def test_send_text_classifies_terminal_errors(
    adapter_no_retry_sleep, monkeypatch, status, code, expected_exc
) -> None:
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=_mount(_error_response(status, code)), timeout=15.0),
    )
    with pytest.raises(expected_exc):
        await adapter_no_retry_sleep.send_text("+5511999999999", "x")


async def test_send_text_rate_limit_is_retried_then_succeeds(
    adapter_no_retry_sleep, monkeypatch
) -> None:
    responses = iter(
        [
            _error_response(429, code=4, extra_headers={"Retry-After": "1"}),
            _ok_response(),
        ]
    )

    def transport(_request):
        return next(responses)

    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport), timeout=15.0),
    )
    r = await adapter_no_retry_sleep.send_text("+5511999999999", "x")
    assert r.external_id  # second attempt succeeded


async def test_send_text_rate_limit_exhausted_raises(adapter_no_retry_sleep, monkeypatch) -> None:
    # Three rate-limited responses → tenacity exhausts → raises last exception
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(
            transport=_mount(_error_response(429, code=4, extra_headers={"Retry-After": "1"})),
            timeout=15.0,
        ),
    )
    with pytest.raises(RateLimitError):
        await adapter_no_retry_sleep.send_text("+5511999999999", "x")


async def test_send_text_5xx_is_classified_transient_and_retried(
    adapter_no_retry_sleep, monkeypatch
) -> None:
    responses = iter(
        [
            _error_response(503, code=2, message="service unavailable"),
            _error_response(503, code=2, message="service unavailable"),
            _ok_response(),
        ]
    )

    def transport(_request):
        return next(responses)

    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport), timeout=15.0),
    )
    r = await adapter_no_retry_sleep.send_text("+5511999999999", "x")
    assert r.external_id  # third attempt succeeded


async def test_send_text_5xx_exhausted_raises_transient(
    adapter_no_retry_sleep, monkeypatch
) -> None:
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=_mount(_error_response(503, code=2)), timeout=15.0),
    )
    with pytest.raises(TransientError):
        await adapter_no_retry_sleep.send_text("+5511999999999", "x")
