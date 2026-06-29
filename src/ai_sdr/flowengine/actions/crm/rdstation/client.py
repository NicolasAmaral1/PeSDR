"""HTTP client for RD Station CRM v1 API.

API base: https://crm.rdstation.com/api/v1
Auth: query param `?token=<TokenDeInstância>`

Endpoints used at MVP:
  GET  /contacts?token=...&phone=<E164>      → search contact by phone
  POST /contacts?token=...                   → create contact
  PUT  /contacts/{id}?token=...              → update contact
  POST /deals?token=...                      → create deal
  PATCH /deals/{id}?token=...                → update deal
  POST /deals/{id}/win?token=...             → mark deal as won
  POST /deals/{id}/loss?token=...            → mark deal as lost

Retry policy: tenacity 3 attempts, exp backoff. 429 honors `Retry-After`
if RD returns one; otherwise 1s/2s/4s. 5xx retries; 4xx (other than 429)
surfaces as a typed error.
"""

from __future__ import annotations

from typing import Any

import httpx
import tenacity


class RDStationAuthError(Exception):
    """Token rejected (401/403)."""


class RDStationValidationError(Exception):
    """RD rejected the payload (422)."""


class RDStationTransientError(Exception):
    """5xx or network — retry."""


class RDStationRateLimit(Exception):
    """429 — retry honoring Retry-After."""


BASE_URL = "https://crm.rdstation.com/api/v1"


def _classify(response: httpx.Response) -> None:
    """Raise the right exception per HTTP status. 2xx returns silently."""
    if response.status_code < 300:
        return
    if response.status_code == 429:
        raise RDStationRateLimit(response.headers.get("Retry-After", "1"))
    if response.status_code in (401, 403):
        raise RDStationAuthError(
            f"RD Station auth rejected: {response.status_code} {response.text[:200]}"
        )
    if response.status_code == 422:
        raise RDStationValidationError(response.text[:500])
    if 500 <= response.status_code < 600:
        raise RDStationTransientError(f"{response.status_code} {response.text[:200]}")
    # Other 4xx — surface as auth-like (likely misconfig).
    raise RDStationAuthError(
        f"unexpected status {response.status_code}: {response.text[:200]}"
    )


_RETRY = tenacity.AsyncRetrying(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=4),
    retry=tenacity.retry_if_exception_type(
        (RDStationTransientError, RDStationRateLimit, httpx.TransportError)
    ),
    reraise=True,
)


class RDStationClient:
    def __init__(self, token: str, *, timeout: float = 8.0) -> None:
        self._token = token
        self._client = httpx.AsyncClient(base_url=BASE_URL, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> RDStationClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # -- contacts -------------------------------------------------------------

    async def search_contact_by_phone(self, phone_e164: str) -> dict[str, Any] | None:
        """Return the first contact whose phone matches, or None."""
        async for attempt in _RETRY:
            with attempt:
                resp = await self._client.get(
                    "/contacts",
                    params={"token": self._token, "phone": phone_e164},
                )
                _classify(resp)
                body = resp.json()
                contacts = body.get("contacts") if isinstance(body, dict) else None
                if isinstance(contacts, list) and contacts:
                    return contacts[0]
                return None
        return None

    async def create_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        async for attempt in _RETRY:
            with attempt:
                resp = await self._client.post(
                    "/contacts", params={"token": self._token}, json=payload
                )
                _classify(resp)
                return resp.json()
        raise RDStationTransientError("create_contact: exhausted retries")

    async def update_contact(
        self, contact_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async for attempt in _RETRY:
            with attempt:
                resp = await self._client.put(
                    f"/contacts/{contact_id}",
                    params={"token": self._token},
                    json=payload,
                )
                _classify(resp)
                return resp.json()
        raise RDStationTransientError("update_contact: exhausted retries")

    # -- deals ----------------------------------------------------------------

    async def create_deal(self, payload: dict[str, Any]) -> dict[str, Any]:
        async for attempt in _RETRY:
            with attempt:
                resp = await self._client.post(
                    "/deals", params={"token": self._token}, json=payload
                )
                _classify(resp)
                return resp.json()
        raise RDStationTransientError("create_deal: exhausted retries")

    async def patch_deal(
        self, deal_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async for attempt in _RETRY:
            with attempt:
                resp = await self._client.patch(
                    f"/deals/{deal_id}",
                    params={"token": self._token},
                    json=payload,
                )
                _classify(resp)
                return resp.json()
        raise RDStationTransientError("patch_deal: exhausted retries")

    async def mark_deal_won(self, deal_id: str) -> dict[str, Any]:
        async for attempt in _RETRY:
            with attempt:
                resp = await self._client.post(
                    f"/deals/{deal_id}/win", params={"token": self._token}
                )
                _classify(resp)
                return resp.json()
        raise RDStationTransientError("mark_deal_won: exhausted retries")

    async def mark_deal_lost(self, deal_id: str) -> dict[str, Any]:
        async for attempt in _RETRY:
            with attempt:
                resp = await self._client.post(
                    f"/deals/{deal_id}/loss", params={"token": self._token}
                )
                _classify(resp)
                return resp.json()
        raise RDStationTransientError("mark_deal_lost: exhausted retries")

    # -- notes ----------------------------------------------------------------

    async def add_note_to_contact(
        self, contact_id: str, note_text: str
    ) -> dict[str, Any]:
        """RD Station: notes can be attached via the contact's note collection.
        Endpoint shape may vary; we POST to /notes with contact_id linkage.
        """
        async for attempt in _RETRY:
            with attempt:
                resp = await self._client.post(
                    "/notes",
                    params={"token": self._token},
                    json={"contact_id": contact_id, "text": note_text},
                )
                _classify(resp)
                return resp.json()
        raise RDStationTransientError("add_note: exhausted retries")
