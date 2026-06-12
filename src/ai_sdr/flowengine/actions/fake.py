"""LoggingActionAdapter — dev/test fake (FE-03c §7.4).

Deterministic for tests. Doesn't touch any external system; logs and
returns a fake external_id derived from sha256(params).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ai_sdr.flowengine.actions.base import ActionAdapter, ActionResult
from ai_sdr.flowengine.actions.registry import register

logger = logging.getLogger(__name__)


@register
class LoggingActionAdapter(ActionAdapter):
    name = "logging"

    async def execute(self, *, handler: str, params: dict[str, Any]) -> ActionResult:
        logger.info(
            "logging_adapter.executed tenant=%s handler=%s params=%s",
            getattr(self.tenant, "slug", "?"),
            handler,
            params,
        )
        canonical = json.dumps(params, sort_keys=True, default=str)
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:8]
        fake_id = f"fake-{handler}-{digest}"
        return ActionResult(external_id=fake_id, detail={"echo": params})
