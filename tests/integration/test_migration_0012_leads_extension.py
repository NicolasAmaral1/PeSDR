"""Verifies migration 0012 added identity fields to leads table."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_leads_table_has_new_identity_columns(db_session: AsyncSession) -> None:
    """All 9 new columns exist with expected types and defaults."""
    result = await db_session.execute(
        text(
            """
            SELECT column_name, data_type, column_default, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'leads'
              AND column_name IN (
                  'channel_identifiers', 'display_name', 'profile',
                  'profile_last_updated', 'long_term_memory_enabled',
                  'risk_level', 'risk_level_since', 'risk_level_reason',
                  'acquisition_metadata'
              )
            ORDER BY column_name
            """
        )
    )
    rows = {r[0]: r for r in result.all()}
    assert set(rows.keys()) == {
        "channel_identifiers",
        "display_name",
        "profile",
        "profile_last_updated",
        "long_term_memory_enabled",
        "risk_level",
        "risk_level_since",
        "risk_level_reason",
        "acquisition_metadata",
    }
    assert rows["risk_level"][2] is not None  # has a default
    assert "normal" in rows["risk_level"][2]
    assert rows["long_term_memory_enabled"][2] is not None
    assert "false" in rows["long_term_memory_enabled"][2].lower()


@pytest.mark.asyncio
async def test_leads_risk_level_check_constraint_rejects_invalid(
    db_session: AsyncSession,
) -> None:
    """risk_level CHECK constraint blocks unknown values."""
    # First we need a tenant context
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:id, :s, :n)"),
        {"id": tenant_id, "s": f"test-{tenant_id.hex[:8]}", "n": "test"},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )

    with pytest.raises(Exception) as excinfo:
        await db_session.execute(
            text(
                "INSERT INTO leads (tenant_id, risk_level) "
                "VALUES (:tid, 'malicious_value')"
            ),
            {"tid": tenant_id},
        )
    assert "ck_leads_risk_level" in str(excinfo.value).lower() or "check" in str(
        excinfo.value
    ).lower()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_leads_accepts_full_identity_payload(db_session: AsyncSession) -> None:
    """Insert lead with all new fields populated; round-trip works."""
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:id, :s, :n)"),
        {"id": tenant_id, "s": f"test-{tenant_id.hex[:8]}", "n": "test"},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    lead_id = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO leads (
                id, tenant_id, channel_identifiers, display_name,
                profile, long_term_memory_enabled, risk_level,
                risk_level_reason, acquisition_metadata
            ) VALUES (
                :id, :tid, CAST(:ci AS JSONB), :dn,
                CAST(:p AS JSONB), :lt, :rl, :rr, CAST(:am AS JSONB)
            )
            """
        ),
        {
            "id": lead_id,
            "tid": tenant_id,
            "ci": json.dumps({"whatsapp": "+5511999999999"}),
            "dn": "Test Lead",
            "p": json.dumps({"likes": "coffee"}),
            "lt": False,
            "rl": "elevated",
            "rr": "spamming",
            "am": json.dumps({"utm_source": "google"}),
        },
    )
    result = await db_session.execute(
        text("SELECT risk_level, channel_identifiers->>'whatsapp' FROM leads WHERE id = :id"),
        {"id": lead_id},
    )
    row = result.one()
    assert row[0] == "elevated"
    assert row[1] == "+5511999999999"
    await db_session.rollback()
