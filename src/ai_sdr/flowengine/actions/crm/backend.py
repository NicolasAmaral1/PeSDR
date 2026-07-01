"""CRMBackend ABC — vendor-agnostic contract for CRM write operations.

Each vendor backend (RDStation, HubSpot, Pipedrive, ...) implements the
same 4 handlers. The CRMActionAdapter dispatches to the configured
backend based on `tenant.yaml > crm.provider`.

Idempotency contract:
  - Backends MUST be safe to re-call with the same inputs (worker can retry).
  - FE-03c dispatcher's UNIQUE (talk, field, value_hash) is the first
    line of defense — same field + same value = no re-dispatch.
  - Backends add a second line: lookup Lead.crm_refs.<provider>.contact_id
    locally → if present, update remote instead of create.
  - Third line: if local cache misses but the remote ALREADY has the
    contact (e.g., manual entry by the operator), the backend does a
    remote search before falling back to create.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from ai_sdr.flowengine.actions.base import ActionResult
from ai_sdr.flowengine.actions.crm.canonical import (
    ContactCanonical,
    DealCanonical,
    DealStage,
)


class CRMBackend(ABC):
    """Per-vendor backend implementing the canonical CRM handler set."""

    provider: str  # class attribute, e.g. "rdstation"

    @abstractmethod
    async def create_or_update_contact(
        self, *, lead_id: uuid.UUID, contact: ContactCanonical
    ) -> ActionResult:
        """Upsert contact identified by phone (primary key social)."""

    @abstractmethod
    async def create_or_update_deal(
        self,
        *,
        lead_id: uuid.UUID,
        contact_external_id: str,
        deal: DealCanonical,
    ) -> ActionResult:
        """Upsert deal identified by (contact, product)."""

    @abstractmethod
    async def update_deal_stage(
        self, *, deal_external_id: str, stage: DealStage
    ) -> ActionResult:
        """Map canonical stage → vendor stage_id and patch the deal."""

    @abstractmethod
    async def record_qualification_note(
        self, *, contact_external_id: str, note: str
    ) -> ActionResult:
        """Append a note to the contact (free-form qualification summary)."""
