"""pipeline.run_turn forwards tenant.humanization to sender (FE-03b Task 10)."""

from __future__ import annotations

import inspect

from ai_sdr.flowengine.pipeline import run_turn


def test_run_turn_signature_includes_tenant_cfg():
    """run_turn now accepts tenant_cfg for humanization (and future tenant-config needs)."""
    sig = inspect.signature(run_turn)
    assert "tenant" in sig.parameters
    assert "tenant_cfg" in sig.parameters
