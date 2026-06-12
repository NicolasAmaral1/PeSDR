"""End-of-plan: all FE-01a migrations apply + roll back cleanly.

Run manually with a *fresh* database. This test is marked so CI can opt
in selectively. The body uses alembic's Python API to run a full
upgrade + downgrade roundtrip on a transient database URL provided via
the env var TEST_FRESH_DB_URL.
"""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config


@pytest.mark.fresh_db
def test_upgrade_head_then_downgrade_to_0011() -> None:
    test_url = os.environ.get("TEST_FRESH_DB_URL")
    if not test_url:
        pytest.skip("TEST_FRESH_DB_URL not set — skipping fresh-DB acceptance")

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", test_url)

    command.upgrade(cfg, "head")
    # Sanity: head is 0023
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    assert head == "0023_add_tenant_architecture_version"

    command.downgrade(cfg, "0011_outbound_messages")
