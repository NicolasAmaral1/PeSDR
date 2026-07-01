"""RDStationCRMBackend — 3-tier idempotency + happy paths (client mocked).

Tests the backend's contract against the RD Station CRM v1 API. Instead of
mocking HTTP with respx (extra dep), we monkey-patch the RDStationClient
methods directly — the client itself is unit-tested by its integration
path, and here we care about the backend's LOGIC (which tier hits which
endpoint, whether Lead.crm_refs gets updated after each write).

Coverage:
  - `create_or_update_contact` 3-tier fallback:
      1. Lead.crm_refs.rdstation.contact_id → PUT update
      2. remote search by phone → PUT update if found
      3. POST create + persist new id to Lead.crm_refs
  - `create_or_update_deal` writes deal + persists deal_id to Lead.crm_refs
  - `update_deal_stage` maps 'won'/'lost' to /win, /loss endpoints
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.db.session import get_sessionmaker
from ai_sdr.flowengine.actions.crm.canonical import ContactCanonical, DealCanonical
from ai_sdr.flowengine.actions.crm.rdstation.backend import RDStationCRMBackend
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.schemas.tenant_yaml import RDStationCRMConfig

pytestmark = pytest.mark.integration


def _cfg() -> RDStationCRMConfig:
    return RDStationCRMConfig(
        token_ref="secrets/rd_token",
        pipeline_id="pipeline-123",
        stage_mapping={
            "open": "stage-open-id",
            "won": "stage-open-id",  # informational — RD marks win via endpoint
            "lost": "stage-open-id",
        },
        custom_field_mapping={"faturamento_mensal_faixa": "cf-fat-id"},
    )


def _backend() -> RDStationCRMBackend:
    return RDStationCRMBackend(_cfg(), {"rd_token": "TEST_TOKEN"})


async def _seed_lead(db_session) -> Lead:
    tenant = Tenant(slug=f"rd-{uuid.uuid4().hex[:6]}", display_name="RD")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    lead = Lead(
        tenant_id=tenant.id,
        whatsapp_e164=f"+5511{uuid.uuid4().hex[:9]}",
        status="active",
    )
    db_session.add(lead)
    await db_session.commit()
    return lead


def _mocked_client(
    search_result=None,
    create_contact_result=None,
    update_contact_result=None,
    create_deal_result=None,
    patch_deal_result=None,
    win_result=None,
    lose_result=None,
):
    """Return an AsyncMock stand-in for RDStationClient that supports `async with`."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    if search_result is not None:
        client.search_contact_by_phone = AsyncMock(return_value=search_result)
    if create_contact_result is not None:
        client.create_contact = AsyncMock(return_value=create_contact_result)
    if update_contact_result is not None:
        client.update_contact = AsyncMock(return_value=update_contact_result)
    if create_deal_result is not None:
        client.create_deal = AsyncMock(return_value=create_deal_result)
    if patch_deal_result is not None:
        client.patch_deal = AsyncMock(return_value=patch_deal_result)
    if win_result is not None:
        client.mark_deal_won = AsyncMock(return_value=win_result)
    if lose_result is not None:
        client.mark_deal_lost = AsyncMock(return_value=lose_result)
    return client


@pytest.mark.asyncio
async def test_create_contact_when_no_local_ref_and_no_remote_match(db_session):
    """Tier 3 — no local ref, no remote match → POST /contacts + persist."""
    lead = await _seed_lead(db_session)
    backend = _backend()
    client = _mocked_client(
        search_result=None,
        create_contact_result={"id": "rd-contact-999"},
    )

    with patch.object(backend, "_client", return_value=client):
        result = await backend.create_or_update_contact(
            lead_id=lead.id,
            contact=ContactCanonical(name="Pietra", phones=["+5511999999999"]),
        )

    assert result.external_id == "rd-contact-999"
    assert result.detail is not None
    assert result.detail["action"] == "created"
    client.create_contact.assert_awaited_once()

    sm = get_sessionmaker()
    async with sm() as session:
        refreshed = await session.get(Lead, lead.id)
        assert refreshed is not None
        assert refreshed.crm_refs["rdstation"]["contact_id"] == "rd-contact-999"


@pytest.mark.asyncio
async def test_update_contact_when_local_ref_present(db_session):
    """Tier 1 — Lead.crm_refs already has contact_id → PUT (no search, no create)."""
    lead = await _seed_lead(db_session)
    lead.crm_refs = {"rdstation": {"contact_id": "existing-rd-id"}}
    await db_session.commit()

    backend = _backend()
    client = _mocked_client(
        update_contact_result={"id": "existing-rd-id"},
    )

    with patch.object(backend, "_client", return_value=client):
        result = await backend.create_or_update_contact(
            lead_id=lead.id,
            contact=ContactCanonical(name="Pietra", phones=["+5511999999999"]),
        )

    assert result.external_id == "existing-rd-id"
    assert result.detail is not None
    assert result.detail["action"] == "updated"
    client.update_contact.assert_awaited_once_with(
        "existing-rd-id", client.update_contact.await_args.args[1]
    )
    # Tier 2 (search) skipped when Tier 1 hits.
    client.search_contact_by_phone.assert_not_called()


@pytest.mark.asyncio
async def test_update_contact_when_remote_lookup_finds_match(db_session):
    """Tier 2 — no local ref, but phone matches remote → adopt + PUT."""
    lead = await _seed_lead(db_session)
    backend = _backend()
    client = _mocked_client(
        search_result={"id": "remote-id-777"},
        update_contact_result={"id": "remote-id-777"},
    )

    with patch.object(backend, "_client", return_value=client):
        result = await backend.create_or_update_contact(
            lead_id=lead.id,
            contact=ContactCanonical(name="Pietra", phones=["+5511999999999"]),
        )

    assert result.external_id == "remote-id-777"
    assert result.detail is not None
    assert result.detail["action"] == "updated_via_remote_match"
    client.create_contact.assert_not_called()


@pytest.mark.asyncio
async def test_create_deal_persists_deal_id_to_local_ref(db_session):
    """create_or_update_deal → POST /deals + Lead.crm_refs.rdstation.deal_id set."""
    lead = await _seed_lead(db_session)
    lead.crm_refs = {"rdstation": {"contact_id": "contact-abc"}}
    await db_session.commit()

    backend = _backend()
    client = _mocked_client(
        create_deal_result={"id": "deal-xyz"},
    )

    with patch.object(backend, "_client", return_value=client):
        result = await backend.create_or_update_deal(
            lead_id=lead.id,
            contact_external_id="contact-abc",
            deal=DealCanonical(
                product="Mentoria", stage="open", qualification_notes="Faturamento 50-100k"
            ),
        )

    assert result.external_id == "deal-xyz"

    sm = get_sessionmaker()
    async with sm() as session:
        refreshed = await session.get(Lead, lead.id)
        assert refreshed is not None
        assert refreshed.crm_refs["rdstation"]["deal_id"] == "deal-xyz"


@pytest.mark.asyncio
async def test_update_deal_stage_won_hits_win_endpoint(db_session):
    """stage='won' → mark_deal_won (dedicated endpoint, NOT a stage move)."""
    backend = _backend()
    client = _mocked_client(win_result={"id": "deal-xyz", "win": True})

    with patch.object(backend, "_client", return_value=client):
        result = await backend.update_deal_stage(
            deal_external_id="deal-xyz", stage="won"
        )

    assert result.external_id == "deal-xyz"
    client.mark_deal_won.assert_awaited_once_with("deal-xyz")
    client.patch_deal.assert_not_called()


@pytest.mark.asyncio
async def test_update_deal_stage_lost_hits_loss_endpoint(db_session):
    """stage='lost' → mark_deal_lost (dedicated endpoint)."""
    backend = _backend()
    client = _mocked_client(lose_result={"id": "deal-xyz", "win": False})

    with patch.object(backend, "_client", return_value=client):
        result = await backend.update_deal_stage(
            deal_external_id="deal-xyz", stage="lost"
        )

    assert result.external_id == "deal-xyz"
    client.mark_deal_lost.assert_awaited_once_with("deal-xyz")
