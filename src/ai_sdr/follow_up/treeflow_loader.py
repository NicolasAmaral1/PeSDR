"""Load the FollowUpConfig from a TalkFlow's pinned TreeflowVersion.

Returns None when the TreeFlow has no `follow_up:` block. Returns a
parsed FollowUpConfig otherwise. The caller is responsible for
checking `cfg.enabled` before scheduling — this loader is intentionally
liberal (returns even disabled configs) so debug/dry-run can inspect
what's declared without acting on it.
"""

from __future__ import annotations

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.schemas.treeflow_yaml import FollowUpConfig, TreeFlow


async def load_treeflow_follow_up(
    session: AsyncSession,
    talkflow: TalkFlow,
) -> FollowUpConfig | None:
    """Return the parsed FollowUpConfig from the TreeFlow YAML pinned to
    this TalkFlow, or None if no `follow_up:` block exists."""
    tv = await session.get(TreeflowVersion, talkflow.treeflow_version_id)
    if tv is None:
        return None
    parsed = TreeFlow.model_validate(yaml.safe_load(tv.content_yaml))
    return parsed.follow_up
