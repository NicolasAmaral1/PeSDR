"""Per-(tenant, lead) Postgres advisory lock helper.

FlowEngine acquires this lock at the top of run_turn so two concurrent
inbound jobs for the same Lead serialize. We use the transaction-scoped
variant (pg_advisory_xact_lock) so the lock releases automatically when
the surrounding session.begin() block exits.

Key derivation: signed 63-bit integer from hash((tenant_id, lead_id)).
Postgres's signed bigint range is +/- 2^63; truncating to 63 bits with
sign clamp avoids overflow.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _lock_key(tenant_id: uuid.UUID, lead_id: uuid.UUID) -> int:
    """Stable 63-bit signed int from (tenant, lead). Same input -> same key."""
    h = hashlib.sha256(f"{tenant_id}:{lead_id}".encode()).digest()
    # Take first 8 bytes -> unsigned 64-bit -> clamp to signed 63-bit.
    n = int.from_bytes(h[:8], "big", signed=False)
    return n & 0x7FFF_FFFF_FFFF_FFFF


async def acquire_lead_lock(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """Acquire the per-(tenant, lead) lock for the current transaction.

    Blocks until the lock is available. Caller MUST be inside a
    session.begin() block; the lock releases on commit or rollback.
    """
    key = _lock_key(tenant_id, lead_id)
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})
