"""RD Station OAuth2 token management.

RD Station CRM API usa OAuth2 com:
- access_token (curto — ~24h)
- refresh_token (longo — pode rotacionar)

Fluxo inicial (1x): operador roda `scripts/oauth_flow_init.py` pra obter
refresh_token via Authorization Code. Guardado em SOPS.

Runtime: este módulo gerencia access_token cache (in-memory por processo)
+ refresh quando 401.

Open question §11.5 da spec: se refresh retornar NOVO refresh_token (RD Station
faz isso?), preciso persistir. Proposta MVP: alert + worker fail; operador
atualiza SOPS manual. Plano futuro: tabela crm_tokens no DB.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import structlog

from ai_sdr.flowengine.actions.crm.errors import AuthError

log = structlog.get_logger(__name__)


# RD Station OAuth endpoints (CRM v1)
OAUTH_TOKEN_URL = "https://api.rd.services/auth/token"
ACCESS_TOKEN_TTL_BUFFER_S = 60  # refresh 60s antes do expires_at pra evitar race


class RDStationOAuth:
    """Gerencia access_token + refresh.

    Thread-safe? NÃO. Cada processo worker tem sua instância. Race entre 2
    workers simultâneos com mesmo lead é prevenida pela advisory lock do
    FE-03c (pg_advisory_lock).
    """

    def __init__(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token: str | None = None
        self._expires_at: datetime | None = None

    async def get_token(self) -> str:
        """Retorna access_token válido. Refresh transparente se expirado/ausente.

        Raises:
            AuthError: refresh falhou (refresh_token expirado/revogado/inválido).
        """
        # TODO: implementação real
        # now = datetime.now(timezone.utc)
        # if (
        #     self._access_token is not None
        #     and self._expires_at is not None
        #     and self._expires_at > now + timedelta(seconds=ACCESS_TOKEN_TTL_BUFFER_S)
        # ):
        #     return self._access_token
        # await self._refresh()
        # assert self._access_token is not None
        # return self._access_token
        raise NotImplementedError("Fase B T5 — RDStationOAuth.get_token")

    async def _refresh(self) -> None:
        """POST OAUTH_TOKEN_URL com grant_type=refresh_token.

        Body shape:
            {
                "client_id": str,
                "client_secret": str,
                "refresh_token": str,
            }

        Response shape (success):
            {
                "access_token": str,
                "expires_in": int (segundos, ~86400 = 24h),
                "refresh_token": str  # pode mudar! ← caso open question
            }
        """
        # TODO: implementação real
        # async with httpx.AsyncClient(timeout=30) as client:
        #     response = await client.post(
        #         OAUTH_TOKEN_URL,
        #         json={
        #             "client_id": self._client_id,
        #             "client_secret": self._client_secret,
        #             "refresh_token": self._refresh_token,
        #         },
        #     )
        # if response.status_code != 200:
        #     log.error("crm.rdstation.refresh_failed", status=response.status_code)
        #     raise AuthError(f"refresh failed: {response.status_code}")
        #
        # data = response.json()
        # self._access_token = data["access_token"]
        # self._expires_at = (
        #     datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])
        # )
        #
        # # Detect refresh_token rotation (open question §11.5)
        # new_refresh = data.get("refresh_token")
        # if new_refresh and new_refresh != self._refresh_token:
        #     log.warning(
        #         "crm.rdstation.refresh_token_rotated",
        #         hint="update tenants/<slug>/secrets.enc.yaml: rdstation_refresh_token",
        #     )
        #     # MVP: levanta exception terminal pra forçar operador
        #     raise AuthError(
        #         "rdstation rotated refresh_token. Update SOPS secrets and retry."
        #     )
        raise NotImplementedError("Fase B T5 — _refresh")
