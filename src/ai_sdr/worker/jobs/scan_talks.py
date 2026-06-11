"""Background scan: close Talks by inactivity / duration (FE-03b §5.2).

Runs as an arq cron job every 5 minutes (configurable via
WORKER_SCAN_INTERVAL_SECONDS). Cross-tenant — uses BYPASSRLS via
SET LOCAL row_security = off (ai_sdr_app has this privilege; same
pattern as follow_up_scanner from Plano 9).

Per-Talk commit so a crash mid-batch leaves a consistent partial state;
next run picks up the remainder. `WHERE status='active'` filter +
SKIP LOCKED on row select prevent double-close and worker contention.

NOTE on talk start time: spec drafts referenced `talk.opened_at`, but
the actual model column is `talk.created_at` (set via server_default
at Talk creation). We use `created_at` here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ai_sdr.flowengine.treeflow_loader import TreeflowLoadError, load_treeflow_v2
from ai_sdr.models.talk import Talk
from ai_sdr.models.treeflow_version import TreeflowVersion

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    inactive_closed: int
    duration_closed: int


async def scan_active_talks(session: AsyncSession, *, now: datetime) -> ScanResult:
    """Close Talks that hit their TreeFlow's inactivity or duration limit."""
    inactive_closed = 0
    duration_closed = 0

    # Cross-tenant scan: opt out of RLS for the duration of this transaction.
    await session.execute(text("SET LOCAL row_security = off"))

    rows = await session.execute(
        select(Talk, TreeflowVersion)
        .join(TreeflowVersion, Talk.treeflow_version_id == TreeflowVersion.id)
        .where(Talk.status == "active")
        .with_for_update(skip_locked=True)
    )

    for talk, tfv in rows:
        try:
            treeflow = load_treeflow_v2(tfv.content_yaml)
        except TreeflowLoadError as exc:
            logger.warning(
                "scan_talks.treeflow_load_failed talk=%s err=%s",
                talk.id,
                exc,
            )
            continue

        lifecycle = treeflow.talk_lifecycle
        if lifecycle is None:
            continue

        if lifecycle.close_after_inactivity:
            cutoff = now - lifecycle.close_after_inactivity
            if talk.last_message_at < cutoff:
                await _close(session, talk, now, "closed_inactivity", "scan_job")
                inactive_closed += 1
                continue

        if lifecycle.close_after_duration:
            cutoff = now - lifecycle.close_after_duration
            if talk.created_at < cutoff:
                await _close(session, talk, now, "closed_duration", "scan_job")
                duration_closed += 1

    await session.commit()
    logger.info(
        "scan_talks.completed inactive_closed=%d duration_closed=%d",
        inactive_closed,
        duration_closed,
    )
    return ScanResult(
        inactive_closed=inactive_closed,
        duration_closed=duration_closed,
    )


async def _close(
    session: AsyncSession,
    talk: Talk,
    now: datetime,
    status: str,
    closed_by: str,
) -> None:
    talk.status = cast(Any, status)
    talk.closed_at = now
    talk.closed_reason = status
    talk.closed_by = closed_by
    flag_modified(talk, "status")
    logger.info(
        "talk.closed.%s talk=%s by=%s last_message_at=%s created_at=%s",
        status.removeprefix("closed_"),
        talk.id,
        closed_by,
        talk.last_message_at,
        talk.created_at,
    )
