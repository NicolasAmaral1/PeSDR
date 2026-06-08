"""Smoke: the Avelum fixture seeds + the TreeFlow parses."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2

from tests.integration.avelum_helpers import seed_avelum_v2


@pytest.mark.asyncio
async def test_seeds_avelum_v2_with_architecture_v2(db_session: AsyncSession) -> None:
    tenant, tfv = await seed_avelum_v2(db_session)
    assert tenant.architecture_version == 2
    tf = load_treeflow_v2(tfv.content_yaml)
    assert tf.id == "avelum_sdr"
    assert tf.entry_node == "saudacao"
    assert set(tf.nodes.keys()) == {"saudacao", "qualificacao_economica"}
