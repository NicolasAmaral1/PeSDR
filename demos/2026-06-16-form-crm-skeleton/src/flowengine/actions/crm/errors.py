"""Exceptions tipadas do subsistema CRM.

Mapping de status code do vendor → exception → ação do worker:

| Status / situação | Exception PeSDR | Worker action |
|---|---|---|
| 401 (token expirado, refresh pendente) | (interno) | refresh + retry MESMO job |
| 401 (refresh falhou) | AuthError | terminal failure + alert |
| 403 (permissão) | AuthError | terminal + alert |
| 422 (validation, ex: phone inválido) | ValidationError | terminal (não retry — payload ruim) |
| 404 (entidade deletada externamente) | RemoteResourceGone | terminal + marca refs stale |
| 429 (rate limit) | RateLimitError(retry_after_s) | tenacity backoff (3x), respeita Retry-After |
| 5xx, network, timeout | TransientError | tenacity backoff (3x) |
| 5xx persistente após 3x | (escala) | arq retry (3x adicional) → failed |
"""
from __future__ import annotations


class CRMError(Exception):
    """Base de todas as exceções do subsistema CRM."""


class AuthError(CRMError):
    """Token inválido OR refresh falhou. Terminal failure pro worker."""


class RemoteResourceGone(CRMError):
    """Entidade existia em refs locais mas foi deletada no CRM externo (404)."""


class ValidationError(CRMError):
    """422 do vendor — payload semanticamente inválido. Terminal (não retry)."""


class RateLimitError(CRMError):
    """429 com Retry-After. Tenacity retry interno respeita."""

    def __init__(self, retry_after_s: int, message: str = ""):
        self.retry_after_s = retry_after_s
        super().__init__(message or f"rate limited, retry after {retry_after_s}s")


class TransientError(CRMError):
    """5xx, network timeout, conn reset. Retry-safe."""


class UnknownHandlerError(CRMError):
    """Handler string não suportado pelo backend.

    Ex: TreeFlow YAML declara `handler: foo_bar` mas RDStationCRMBackend não
    implementa. Loader emite warning, mas runtime safety-net levanta isso.
    """


class UnknownBackendError(CRMError):
    """Provider name no tenant.yaml não está no CRM_BACKENDS registry."""
