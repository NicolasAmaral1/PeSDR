"""scan_talks must not auto-close human-held talks (Task 7)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.worker.jobs.scan_talks import scan_active_talks

pytestmark = pytest.mark.integration

# Real lifecycle YAML: close_after_inactivity=P7D — a 30-day-old talk would
# normally get closed_inactivity by the scanner.
_LIFECYCLE_YAML = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "avelum_v2_with_lifecycle.yaml"
).read_text()


@pytest.mark.asyncio
async def test_scan_does_not_close_human_talk(db_session, seeded_talk_factory):
    # Seed a human-held talk, then swap its treeflow to one with a real lifecycle.
    old = datetime.now(timezone.utc) - timedelta(days=30)
    talk, tenant = await seeded_talk_factory(handling_mode="human", status="active")

    # Replace the TreeflowVersion content with real lifecycle YAML so the
    # scanner would actually close an ai-mode talk.
    tfv = await db_session.get(TreeflowVersion, talk.treeflow_version_id)
    tfv.content_yaml = _LIFECYCLE_YAML

    talk.last_message_at = old
    await db_session.commit()

    await scan_active_talks(db_session, now=datetime.now(timezone.utc))

    refreshed = await db_session.get(type(talk), talk.id)
    assert refreshed.status == "active", (
        f"human talk was auto-closed to {refreshed.status!r} — scanner must skip human-held talks"
    )
