from pathlib import Path

import pytest

from ai_sdr.tenant_loader.loader import (
    TenantLoader,
    TenantNotFoundError,
)


@pytest.fixture
def loader(tmp_path: Path) -> TenantLoader:
    src = Path("tenants/example/tenant.yaml")
    dest_dir = tmp_path / "tenants" / "example"
    dest_dir.mkdir(parents=True)
    (dest_dir / "tenant.yaml").write_text(src.read_text(), encoding="utf-8")
    return TenantLoader(tenants_dir=tmp_path / "tenants")


def test_load_existing_tenant(loader: TenantLoader) -> None:
    cfg = loader.load("example")
    assert cfg.id == "example"
    assert cfg.display_name == "Example Tenant"
    assert cfg.conversation is not None
    assert cfg.conversation.debounce_ms == 5000


def test_load_caches_result(loader: TenantLoader) -> None:
    cfg1 = loader.load("example")
    cfg2 = loader.load("example")
    assert cfg1 is cfg2


def test_load_unknown_tenant_raises(loader: TenantLoader) -> None:
    with pytest.raises(TenantNotFoundError):
        loader.load("does-not-exist")


def test_reload_bypasses_cache(loader: TenantLoader) -> None:
    cfg1 = loader.load("example")
    cfg2 = loader.reload("example")
    assert cfg1 is not cfg2
    assert cfg1 == cfg2
