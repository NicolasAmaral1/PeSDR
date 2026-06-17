"""Exceptions hierarchy do subsistema forms.

Espelha o pattern de `messaging/errors.py`. Webhook handler mapeia pra HTTP
status code apropriado.
"""
from __future__ import annotations


class FormProviderError(Exception):
    """Base de todas as exceções do subsistema forms."""


class SignatureError(FormProviderError):
    """HMAC/shared_secret inválido.

    Mapeado pra HTTP 401 no webhook handler.
    """


class MalformedPayload(FormProviderError):
    """Payload parseou mas shape inesperado, campo obrigatório ausente,
    phone não-normalizável, etc.

    Mapeado pra HTTP 400 no webhook handler.
    """


class UnknownFormProviderError(FormProviderError):
    """Provider string passado ao factory não existe no registry.

    Mapeado pra HTTP 404 no webhook handler (tenant_slug pode estar OK mas
    provider não suportado).
    """
