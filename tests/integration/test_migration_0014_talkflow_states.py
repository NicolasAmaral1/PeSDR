"""Verifies migration 0014 creates talkflow_states table."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_talkflow_states_columns(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'talkflow_states'
            ORDER BY column_name
            """
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "talk_id",
        "tenant_id",
        "current_node",
        "collected",
        "extracted_facts",
        "messages",
        "history_summary",
        "history_summary_covers_until_turn",
        "active_treatment",
        "objections_handled",
        "talkflow_stack",
        "updated_at",
    }


@pytest.mark.asyncio
async def test_talkflow_state_one_to_one_with_talk(db_session: AsyncSession) -> None:
    """A second talkflow_state insert for the same talk_id must fail (PK uniqueness)."""
    tenant_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    tfv_id = uuid.uuid4()
    talk_id = uuid.uuid4()

    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, 't')"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}"},
    )
    await db_session.execute(
        text("INSERT INTO leads (id, tenant_id) VALUES (:i, :t)"),
        {"i": lead_id, "t": tenant_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO treeflow_versions (id, tenant_id, treeflow_id, version, "
            "content_hash, content_yaml) VALUES (:i, :t, 'tf', '1', 'x', 'y')"
        ),
        {"i": tfv_id, "t": tenant_id},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO talks (id, tenant_id, lead_id, treeflow_id, "
            "treeflow_version_id, status, handling_mode, last_message_at) "
            "VALUES (:i, :t, :l, 'tf', :v, 'active', 'ai', now())"
        ),
        {"i": talk_id, "t": tenant_id, "l": lead_id, "v": tfv_id},
    )

    await db_session.execute(
        text(
            "INSERT INTO talkflow_states (talk_id, tenant_id, current_node, "
            "collected, extracted_facts, messages, objections_handled, talkflow_stack) "
            "VALUES (:t, :tn, 'saudacao', CAST(:c AS JSONB), CAST(:f AS JSONB), "
            "CAST(:m AS JSONB), CAST(:o AS JSONB), CAST(:s AS JSONB))"
        ),
        {
            "t": talk_id,
            "tn": tenant_id,
            "c": "{}",
            "f": "{}",
            "m": "[]",
            "o": "[]",
            "s": "[]",
        },
    )

    with pytest.raises(Exception):
        await db_session.execute(
            text(
                "INSERT INTO talkflow_states (talk_id, tenant_id, current_node, "
                "collected, extracted_facts, messages, objections_handled, talkflow_stack) "
                "VALUES (:t, :tn, 'other', CAST(:c AS JSONB), CAST(:f AS JSONB), "
                "CAST(:m AS JSONB), CAST(:o AS JSONB), CAST(:s AS JSONB))"
            ),
            {
                "t": talk_id,
                "tn": tenant_id,
                "c": "{}",
                "f": "{}",
                "m": "[]",
                "o": "[]",
                "s": "[]",
            },
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_talkflow_state_cascade_on_talk_delete(db_session: AsyncSession) -> None:
    """Deleting the talk removes the talkflow_state."""
    tenant_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    tfv_id = uuid.uuid4()
    talk_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, 't')"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}"},
    )
    await db_session.execute(
        text("INSERT INTO leads (id, tenant_id) VALUES (:i, :t)"),
        {"i": lead_id, "t": tenant_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO treeflow_versions (id, tenant_id, treeflow_id, version, "
            "content_hash, content_yaml) VALUES (:i, :t, 'tf', '1', 'x', 'y')"
        ),
        {"i": tfv_id, "t": tenant_id},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO talks (id, tenant_id, lead_id, treeflow_id, "
            "treeflow_version_id, status, handling_mode, last_message_at) "
            "VALUES (:i, :t, :l, 'tf', :v, 'active', 'ai', now())"
        ),
        {"i": talk_id, "t": tenant_id, "l": lead_id, "v": tfv_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO talkflow_states (talk_id, tenant_id, current_node, "
            "collected, extracted_facts, messages, objections_handled, talkflow_stack) "
            "VALUES (:t, :tn, 'saudacao', CAST('{}' AS JSONB), CAST('{}' AS JSONB), "
            "CAST('[]' AS JSONB), CAST('[]' AS JSONB), CAST('[]' AS JSONB))"
        ),
        {"t": talk_id, "tn": tenant_id},
    )

    await db_session.execute(text("DELETE FROM talks WHERE id = :i"), {"i": talk_id})
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM talkflow_states WHERE talk_id = :t"),
        {"t": talk_id},
    )
    assert result.scalar_one() == 0
    await db_session.rollback()
