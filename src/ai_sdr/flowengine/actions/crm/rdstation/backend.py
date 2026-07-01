"""RDStationCRMBackend — implements CRMBackend against RD Station CRM v1.

Persistence side-effect: after each successful write, updates
`leads.crm_refs.rdstation.{contact_id|deal_id|last_synced_at}` via an
internal session. The TalkFlow runtime / dispatcher session is not
available here (ActionAdapter contract takes only handler + params), so
we open our own session via the global sessionmaker.

Idempotency:
  1. Lookup local: read Lead.crm_refs.rdstation.contact_id → if present,
     PUT update.
  2. Lookup remote: search by phone → if found, persist to local + update.
  3. Create new + persist id to local.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.db.session import get_sessionmaker
from ai_sdr.flowengine.actions.base import ActionResult
from ai_sdr.flowengine.actions.crm.backend import CRMBackend
from ai_sdr.flowengine.actions.crm.canonical import (
    ContactCanonical,
    DealCanonical,
    DealStage,
)
from ai_sdr.flowengine.actions.crm.rdstation.client import (
    RDStationClient,
    RDStationValidationError,
)
from ai_sdr.models.lead import Lead
from ai_sdr.schemas.tenant_yaml import RDStationCRMConfig

log = structlog.get_logger(__name__)


class RDStationCRMBackend(CRMBackend):
    provider = "rdstation"

    def __init__(
        self, cfg: RDStationCRMConfig, secrets: dict[str, str]
    ) -> None:
        self._cfg = cfg
        token_ref = cfg.token_ref.removeprefix("secrets/")
        if token_ref not in secrets:
            raise KeyError(f"secret {token_ref!r} missing from tenant secrets")
        self._token = secrets[token_ref]

    # -- helpers --------------------------------------------------------------

    def _client(self) -> RDStationClient:
        return RDStationClient(self._token)

    async def _read_local_crm_refs(self, lead_id: uuid.UUID) -> dict[str, Any]:
        sm = get_sessionmaker()
        async with sm() as session:
            lead = (
                await session.execute(select(Lead).where(Lead.id == lead_id))
            ).scalar_one_or_none()
            if lead is None:
                return {}
            return dict(lead.crm_refs or {}).get(self.provider, {})

    async def _persist_local_ref(
        self,
        *,
        lead_id: uuid.UUID,
        contact_external_id: str | None = None,
        deal_external_id: str | None = None,
    ) -> None:
        """Update Lead.crm_refs.rdstation with provided external ids + timestamp."""
        sm = get_sessionmaker()
        async with sm() as session:
            lead = (
                await session.execute(select(Lead).where(Lead.id == lead_id))
            ).scalar_one_or_none()
            if lead is None:
                log.warning(
                    "crm.rdstation.lead_vanished_during_ref_update",
                    lead_id=str(lead_id),
                )
                return
            await set_tenant_context(session, lead.tenant_id)

            current = dict(lead.crm_refs or {})
            vendor = dict(current.get(self.provider, {}))
            if contact_external_id is not None:
                vendor["contact_id"] = contact_external_id
            if deal_external_id is not None:
                vendor["deal_id"] = deal_external_id
            vendor["last_synced_at"] = datetime.now(UTC).isoformat()
            current[self.provider] = vendor
            lead.crm_refs = current
            await session.commit()

    def _contact_payload(self, contact: ContactCanonical) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": contact.name}
        if contact.emails:
            payload["emails"] = [{"email": e} for e in contact.emails]
        if contact.phones:
            payload["phones"] = [{"phone": p, "type": "cellphone"} for p in contact.phones]
        if contact.custom_fields and self._cfg.custom_field_mapping:
            cf_payload: list[dict[str, Any]] = []
            for canonical_name, value in contact.custom_fields.items():
                cf_id = self._cfg.custom_field_mapping.get(canonical_name)
                if cf_id:
                    cf_payload.append({"custom_field_id": cf_id, "value": value})
            if cf_payload:
                payload["contact_custom_fields"] = cf_payload
        return payload

    def _deal_payload(
        self, *, contact_id: str, deal: DealCanonical
    ) -> dict[str, Any]:
        stage_id = self._cfg.stage_mapping.get(deal.stage) or self._cfg.stage_mapping["open"]
        payload: dict[str, Any] = {
            "deal": {
                "name": deal.product,
                "deal_stage_id": stage_id,
            },
            "contacts": [{"id": contact_id}],
        }
        if deal.value is not None:
            payload["deal"]["amount_total"] = deal.value
            payload["deal"]["amount_unique"] = deal.value
        if deal.qualification_notes:
            payload["deal"]["prediction_description"] = deal.qualification_notes
        return payload

    # -- handlers -------------------------------------------------------------

    async def create_or_update_contact(
        self, *, lead_id: uuid.UUID, contact: ContactCanonical
    ) -> ActionResult:
        async with self._client() as client:
            existing_refs = await self._read_local_crm_refs(lead_id)
            local_id = existing_refs.get("contact_id")
            payload = self._contact_payload(contact)

            if local_id:
                resp = await client.update_contact(local_id, payload)
                await self._persist_local_ref(
                    lead_id=lead_id, contact_external_id=local_id
                )
                return ActionResult(
                    external_id=local_id, detail={"action": "updated", "raw": resp}
                )

            # Try remote lookup by phone before creating.
            remote: dict[str, Any] | None = None
            if contact.phones:
                try:
                    remote = await client.search_contact_by_phone(contact.phones[0])
                except RDStationValidationError as exc:
                    log.warning(
                        "crm.rdstation.search_failed",
                        lead_id=str(lead_id),
                        err=str(exc),
                    )
            if remote and isinstance(remote.get("id"), str):
                remote_id = remote["id"]
                resp = await client.update_contact(remote_id, payload)
                await self._persist_local_ref(
                    lead_id=lead_id, contact_external_id=remote_id
                )
                return ActionResult(
                    external_id=remote_id,
                    detail={"action": "updated_via_remote_match", "raw": resp},
                )

            resp = await client.create_contact(payload)
            new_id = str(resp.get("id") or "")
            if not new_id:
                raise RDStationValidationError(
                    f"RD response missing id: {resp!r}"
                )
            await self._persist_local_ref(
                lead_id=lead_id, contact_external_id=new_id
            )
            return ActionResult(
                external_id=new_id, detail={"action": "created", "raw": resp}
            )

    async def create_or_update_deal(
        self,
        *,
        lead_id: uuid.UUID,
        contact_external_id: str,
        deal: DealCanonical,
    ) -> ActionResult:
        async with self._client() as client:
            existing_refs = await self._read_local_crm_refs(lead_id)
            local_deal_id = existing_refs.get("deal_id")
            payload = self._deal_payload(contact_id=contact_external_id, deal=deal)

            if local_deal_id:
                resp = await client.patch_deal(local_deal_id, {"deal": payload["deal"]})
                await self._persist_local_ref(
                    lead_id=lead_id, deal_external_id=local_deal_id
                )
                return ActionResult(
                    external_id=local_deal_id, detail={"action": "updated", "raw": resp}
                )

            resp = await client.create_deal(payload)
            new_id = str(resp.get("id") or "")
            if not new_id:
                raise RDStationValidationError(
                    f"RD response missing deal id: {resp!r}"
                )
            await self._persist_local_ref(
                lead_id=lead_id, deal_external_id=new_id
            )
            return ActionResult(
                external_id=new_id, detail={"action": "created", "raw": resp}
            )

    async def update_deal_stage(
        self, *, deal_external_id: str, stage: DealStage
    ) -> ActionResult:
        async with self._client() as client:
            if stage == "won":
                resp = await client.mark_deal_won(deal_external_id)
            elif stage == "lost":
                resp = await client.mark_deal_lost(deal_external_id)
            else:
                stage_id = self._cfg.stage_mapping.get(stage)
                if stage_id is None:
                    raise ValueError(
                        f"stage {stage!r} not in tenant.yaml stage_mapping"
                    )
                resp = await client.patch_deal(
                    deal_external_id, {"deal": {"deal_stage_id": stage_id}}
                )
        return ActionResult(
            external_id=deal_external_id, detail={"action": "stage_updated", "raw": resp}
        )

    async def record_qualification_note(
        self, *, contact_external_id: str, note: str
    ) -> ActionResult:
        async with self._client() as client:
            resp = await client.add_note_to_contact(contact_external_id, note)
        return ActionResult(
            external_id=str(resp.get("id") or ""),
            detail={"action": "note_added", "raw": resp},
        )
