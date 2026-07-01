"""CRM action subsystem (spec 2026-06-16 §4, ADR 2026-06-12 Fase 1).

Registers `CRMActionAdapter` (name="crm") on import. Backend dispatch
happens inside the adapter based on `tenant.crm.provider`.
"""

from __future__ import annotations

from ai_sdr.flowengine.actions.crm import adapter as _adapter  # noqa: F401
