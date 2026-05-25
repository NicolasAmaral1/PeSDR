"""Placeholder — real implementation lands in Plano 5 Task 13."""

from __future__ import annotations

from collections.abc import Mapping

from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.messaging.factory import register_provider
from ai_sdr.schemas.tenant_yaml import MessagingConfig


@register_provider("whatsapp_cloud")
def _build_whatsapp_cloud(cfg: MessagingConfig, secrets: Mapping[str, str]) -> MessagingAdapter:
    """Replaced in Task 13 with the real WhatsAppCloudAPIAdapter."""
    raise NotImplementedError(
        "WhatsAppCloudAPIAdapter lands in Plano 5 Task 13. "
        "Factory dispatch is wired but the impl is pending."
    )
