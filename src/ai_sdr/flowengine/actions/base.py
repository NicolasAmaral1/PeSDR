"""ActionAdapter contract (FE-03c §7.1)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_sdr.schemas.tenant_yaml import TenantConfig


@dataclass
class ActionResult:
    """Returned by ActionAdapter.execute on success."""

    external_id: str | None
    detail: dict[str, Any] | None = None


class ActionAdapter(ABC):
    """Contract for FE-03c action adapters.

    Idempotency note: workers may retry an execute() call after partial
    crashes. Adapters MUST be safe to re-call — either idempotent natively,
    or by detecting prior execution via external system query.
    """

    name: str  # class attribute; used as registry key

    def __init__(self, tenant_config: TenantConfig, secrets: dict[str, str]) -> None:
        self.tenant = tenant_config
        self.secrets = secrets

    @abstractmethod
    async def execute(self, *, handler: str, params: dict[str, Any]) -> ActionResult:
        """Run the action. Raise on failure (worker handles retry)."""
        ...
