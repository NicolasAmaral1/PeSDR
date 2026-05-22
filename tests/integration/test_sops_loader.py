import shutil
import subprocess
from pathlib import Path

import pytest

from ai_sdr.secrets.sops_loader import SopsDecryptError, SopsLoader


@pytest.fixture
def loader(tmp_path: Path) -> SopsLoader:
    sops_cfg = Path(".sops.yaml")
    secrets_src = Path("tenants/example/secrets.enc.yaml")
    shutil.copy(sops_cfg, tmp_path / ".sops.yaml")
    (tmp_path / "tenants" / "example").mkdir(parents=True)
    shutil.copy(secrets_src, tmp_path / "tenants" / "example" / "secrets.enc.yaml")
    return SopsLoader(tenants_dir=tmp_path / "tenants", project_root=tmp_path)


@pytest.mark.integration
def test_sops_binary_available() -> None:
    """SOPS must be installed on the host."""
    result = subprocess.run(  # noqa: S603
        ["sops", "--version"],  # noqa: S607
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


@pytest.mark.integration
def test_decrypt_returns_plaintext_dict(loader: SopsLoader) -> None:
    secrets = loader.load("example")
    assert secrets["anthropic_key"] == "sk-ant-FAKE-FOR-TEST-ONLY"
    assert secrets["rd_station_token"] == "fake-rd-token"


@pytest.mark.integration
def test_decrypt_missing_file_raises(loader: SopsLoader) -> None:
    with pytest.raises(SopsDecryptError):
        loader.load("does-not-exist")
