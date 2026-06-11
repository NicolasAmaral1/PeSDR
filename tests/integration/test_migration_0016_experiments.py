"""Verifies migration 0016 creates experiments table (reserved slot)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_experiments_table_columns(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'experiments' ORDER BY column_name"
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "id",
        "tenant_id",
        "name",
        "key",
        "variants",
        "status",
        "eligibility_rules",
        "started_at",
        "expected_end",
        "target_sample_size",
        "primary_success_metric",
        "secondary_metrics",
        "exclusivity",
        "priority",
        "on_conclusion_behavior",
        "winner",
        "statistical_confidence",
        "analysis_notes",
        "created_at",
    }


@pytest.mark.asyncio
async def test_experiments_insert_round_trip(db_session: AsyncSession) -> None:
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, 't')"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}"},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO experiments (tenant_id, name, key, variants, status, "
            "eligibility_rules, target_sample_size, primary_success_metric, "
            "secondary_metrics, exclusivity, priority, on_conclusion_behavior) "
            "VALUES (:t, 'exp1', 'exp1_key', CAST(:v AS JSONB), 'draft', "
            "CAST(:e AS JSONB), 100, 'conversion_rate', CAST(:s AS JSONB), "
            "'exclusive', 0, 'preserve_running_talks')"
        ),
        {
            "t": tenant_id,
            "v": json.dumps({"A": {"treeflow_version_id": str(uuid.uuid4()), "split": 0.5}}),
            "e": json.dumps([]),
            "s": json.dumps([]),
        },
    )
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM experiments WHERE tenant_id = :t"),
        {"t": tenant_id},
    )
    assert result.scalar_one() == 1
    await db_session.rollback()
