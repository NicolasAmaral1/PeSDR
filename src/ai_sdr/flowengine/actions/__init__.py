"""FlowEngine action framework (FE-03c).

Side-effect imports below register adapters into the registry.
"""

from __future__ import annotations

from ai_sdr.flowengine.actions import fake  # noqa: F401 — registers LoggingActionAdapter
from ai_sdr.flowengine.actions.crm import (  # noqa: F401 — registers CRMActionAdapter
    adapter as _crm_adapter,
)
