"""worker.jobs.scan_talks scan_active_talks (FE-03b Task 15)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_scan_active_talks_smoke(db_session):
    """Smoke: function callable, returns ScanResult dataclass."""
    from datetime import datetime, timezone

    from ai_sdr.worker.jobs.scan_talks import ScanResult, scan_active_talks

    result = await scan_active_talks(
        db_session,
        now=datetime.now(timezone.utc),
    )
    assert isinstance(result, ScanResult)
    assert result.inactive_closed >= 0
    assert result.duration_closed >= 0
