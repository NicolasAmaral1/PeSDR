"""E2E reference contract: scan_active_talks closes by inactivity (FE-03b Task 17)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio


async def test_inactive_talk_closed_by_scan(
    async_session,
    run_turn_harness,
):
    talk = await run_turn_harness.talk()
    talk.last_message_at = datetime.now(timezone.utc) - timedelta(days=8)
    await async_session.commit()
    from ai_sdr.worker.jobs.scan_talks import scan_active_talks

    await scan_active_talks(async_session, now=datetime.now(timezone.utc))
    await async_session.refresh(talk)
    assert talk.status == "closed_inactivity"
