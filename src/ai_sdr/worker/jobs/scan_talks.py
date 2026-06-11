"""Background scan: close Talks by inactivity / duration (FE-03b §5.2).

Runs as an arq cron job every 5 minutes (configurable via
WORKER_SCAN_INTERVAL_SECONDS). Cross-tenant — uses BYPASSRLS via
SET LOCAL row_security = off (ai_sdr_app has this privilege; same
pattern as follow_up_scanner from Plano 9).

Two-phase: Phase A reads candidates cross-tenant without locks; Phase B
opens a fresh transaction per Talk (SET LOCAL row_security=off +
SELECT FOR UPDATE SKIP LOCKED + close + commit). This guarantees:
- Crash mid-batch leaves prior closures committed (no rollback).
- SKIP LOCKED skips Talks another worker is already processing.
- Row locks held only briefly per Talk.

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

from ai_sdr.flowengine.treeflow_loader import TreeflowLoadError, load_treeflow_v2
from ai_sdr.models.talk import Talk
from ai_sdr.models.treeflow_version import TreeflowVersion

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    inactive_closed: int
    duration_closed: int


async def scan_active_talks(session: AsyncSession, *, now: datetime) -> ScanResult:
    """Close Talks that hit their TreeFlow's inactivity or duration limit.

    Two-phase: first read all candidates cross-tenant (no locks), then per-Talk
    open a fresh transaction with SET LOCAL row_security=off + SELECT FOR UPDATE
    SKIP LOCKED + close + commit. This guarantees:
    - Crash mid-batch leaves prior closures committed (no rollback).
    - SKIP LOCKED skips Talks another worker is already processing.
    - Row locks held only briefly per Talk.
    """
    inactive_closed = 0
    duration_closed = 0

    # Phase A: read all candidates cross-tenant (no FOR UPDATE).
    await session.execute(text("SET LOCAL row_security = off"))
    candidates = (
        await session.execute(
            select(
                Talk.id,
                TreeflowVersion.content_yaml,
                Talk.last_message_at,
                Talk.created_at,
            )
            .join(TreeflowVersion, Talk.treeflow_version_id == TreeflowVersion.id)
            .where(Talk.status == "active")
        )
    ).all()
    # End the read transaction; per-Talk transactions follow.
    await session.commit()

    for talk_id, content_yaml, last_message_at, created_at in candidates:
        try:
            treeflow = load_treeflow_v2(content_yaml)
        except TreeflowLoadError as exc:
            logger.warning(
                "scan_talks.treeflow_load_failed talk=%s err=%s",
                talk_id,
                exc,
            )
            continue

        lifecycle = treeflow.talk_lifecycle
        if lifecycle is None:
            continue

        # Evaluate which close (if any) applies. Use cached last_message_at/created_at
        # from Phase A — the actual Talk row will be re-checked under FOR UPDATE
        # before close.
        close_status: str | None = None
        if (
            lifecycle.close_after_inactivity
            and last_message_at < now - lifecycle.close_after_inactivity
        ):
            close_status = "closed_inactivity"
        if (
            close_status is None
            and lifecycle.close_after_duration
            and created_at < now - lifecycle.close_after_duration
        ):
            close_status = "closed_duration"
        if close_status is None:
            continue

        # Phase B: per-Talk transaction. SET LOCAL again because it's
        # transaction-scoped and we committed above.
        await session.execute(text("SET LOCAL row_security = off"))
        locked_talk = (
            await session.execute(
                select(Talk)
                .where(Talk.id == talk_id, Talk.status == "active")
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if locked_talk is None:
            # Another worker grabbed it or status already changed.
            await session.commit()
            continue
        await _close(session, locked_talk, now, close_status, "scan_job")
        await session.commit()
        if close_status == "closed_inactivity":
            inactive_closed += 1
        else:
            duration_closed += 1

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
    logger.info(
        "talk.closed.%s talk=%s by=%s last_message_at=%s created_at=%s",
        status.removeprefix("closed_"),
        talk.id,
        closed_by,
        talk.last_message_at,
        talk.created_at,
    )
