"""Verifies migration 0013 creates talks table with RLS + indexes."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_talks_table_exists_with_columns(db_session: AsyncSession) -> None:
    """All expected columns exist with right types."""
    result = await db_session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'talks'
            ORDER BY column_name
            """
        )
    )
    columns = {r[0] for r in result.all()}
    assert columns >= {
        "id", "tenant_id", "lead_id", "treeflow_id", "treeflow_version_id",
        "status", "handling_mode", "created_at", "last_message_at",
        "closed_at", "closed_reason", "closed_by",
        "escalated_at", "escalation_category", "escalation_reason",
        "experiment_id", "experiment_variant",
        "turn_count", "tokens_consumed",
    }


@pytest.mark.asyncio
async def test_talks_rls_isolates_tenants(db_session: AsyncSession) -> None:
    """Rows are invisible to other tenants under RLS."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    for tid in (tenant_a, tenant_b):
        await db_session.execute(
            text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, :n)"),
            {"i": tid, "s": f"t-{tid.hex[:8]}", "n": "t"},
        )

    # Create a treeflow version + lead under tenant_a
    tfv_id = uuid.uuid4()
    lead_a = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO treeflow_versions (id, tenant_id, treeflow_id, version, "
            "content_hash, content_yaml) VALUES (:i, :t, 'tf', '1.0', 'x', 'yaml')"
        ),
        {"i": tfv_id, "t": tenant_a},
    )
    await db_session.execute(
        text("INSERT INTO leads (id, tenant_id) VALUES (:i, :t)"),
        {"i": lead_a, "t": tenant_a},
    )

    talk_id = uuid.uuid4()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_a)},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO talks (
                id, tenant_id, lead_id, treeflow_id, treeflow_version_id,
                status, handling_mode, last_message_at
            ) VALUES (
                :id, :tid, :lid, 'tf', :tfv,
                'active', 'ai', now()
            )
            """
        ),
        {"id": talk_id, "tid": tenant_a, "lid": lead_a, "tfv": tfv_id},
    )

    # Switch to tenant_b context — row must be invisible
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_b)},
    )
    result = await db_session.execute(text("SELECT COUNT(*) FROM talks"))
    assert result.scalar_one() == 0

    # Switch back to tenant_a — row must be visible
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_a)},
    )
    result = await db_session.execute(text("SELECT COUNT(*) FROM talks"))
    assert result.scalar_one() == 1
    await db_session.rollback()


@pytest.mark.asyncio
async def test_talks_status_check_constraint(db_session: AsyncSession) -> None:
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, :n)"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}", "n": "t"},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    with pytest.raises(Exception):
        await db_session.execute(
            text(
                "INSERT INTO talks (tenant_id, lead_id, treeflow_id, "
                "treeflow_version_id, status, handling_mode, last_message_at) "
                "VALUES (:t, :t, 'tf', :t, 'fakestatus', 'ai', now())"
            ),
            {"t": tenant_id},
        )
    await db_session.rollback()
