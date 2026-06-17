"""HTTP client low-level pra RD Station CRM API v1.

Responsabilidade:
- Bearer auth (token vem do RDStationOAuth)
- Tenacity retry exponencial (3x) pra TransientError/RateLimitError
- Error classification por status code → exception PeSDR

Endpoints usados:
- GET /api/v1/contacts?email=... ou ?phone=... (search)
- POST /api/v1/contacts (create)
- PATCH /api/v1/contacts/{id} (update)
- POST /api/v1/deals (create)
- PATCH /api/v1/deals/{id} (update)
- POST /api/v1/deals/{id}/comments (notes)
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ai_sdr.flowengine.actions.crm.errors import (
    AuthError,
    RateLimitError,
    RemoteResourceGone,
    TransientError,
    ValidationError,
)
from ai_sdr.flowengine.actions.crm.rdstation.oauth import RDStationOAuth

log = structlog.get_logger(__name__)

CRM_BASE_URL = "https://crm.rdstation.com/api/v1"
DEFAULT_TIMEOUT_S = 30


class RDStationClient:
    """Async HTTP client com auth + retry + error mapping."""

    def __init__(self, oauth: RDStationOAuth, base_url: str = CRM_BASE_URL) -> None:
        self.oauth = oauth
        self.base_url = base_url

    async def search_contact_by_phone(
        self, phone_e164: str
    ) -> dict[str, Any] | None:
        """GET /contacts?phone=... — retorna primeiro match ou None."""
        # TODO: implementação real com retry
        raise NotImplementedError("Fase B T6/T7 — search_contact_by_phone")

    async def create_contact(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /contacts — retorna contact criado (com id)."""
        raise NotImplementedError("Fase B T6/T7 — create_contact")

    async def update_contact(
        self, contact_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """PATCH /contacts/{id}."""
        raise NotImplementedError("Fase B T6/T7 — update_contact")

    async def create_deal(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /deals — retorna deal criado (com id)."""
        raise NotImplementedError("Fase B T6/T8 — create_deal")

    async def update_deal(
        self, deal_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """PATCH /deals/{id}."""
        raise NotImplementedError("Fase B T6/T8 — update_deal")

    async def add_deal_comment(self, deal_id: str, comment: str) -> dict[str, Any]:
        """POST /deals/{id}/comments — record_qualification_note."""
        raise NotImplementedError("Fase B T6/T9 — add_deal_comment")

    # ─── infra ────────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Wrapper com auth + retry + error classification.

        Roteiro:
        1. async for attempt in AsyncRetrying(...):
           - get token via self.oauth.get_token()
           - httpx.AsyncClient request com Authorization: Bearer
           - if 401 e attempt 1: invalidate token, force refresh, retry
           - if 5xx, 429: raise TransientError/RateLimitError (tenacity catch)
           - if 4xx: raise typed terminal exception (não retry)
           - if 2xx: return response.json()
        """
        raise NotImplementedError("Fase B T6 — _request com tenacity")

    def _classify_error(self, status: int, body: dict[str, Any]) -> Exception:
        """Mapeia HTTP status → exception PeSDR.

        Ver tabela em flowengine/actions/crm/errors.py.
        """
        # TODO: implementação real
        # if status in (401, 403):
        #     return AuthError(f"rd_station auth failed: {status} {body}")
        # if status == 404:
        #     return RemoteResourceGone(f"resource gone: {body}")
        # if status == 422:
        #     return ValidationError(f"validation error: {body}")
        # if status == 429:
        #     retry_after = int(body.get("retry_after", 30))
        #     return RateLimitError(retry_after_s=retry_after)
        # if status >= 500:
        #     return TransientError(f"rd_station 5xx: {status} {body}")
        # if status >= 400:
        #     return ValidationError(f"rd_station 4xx: {status} {body}")
        # return TransientError(f"rd_station unexpected: {status}")
        raise NotImplementedError("Fase B T6 — _classify_error")
