from pathlib import Path

import pytest

from ai_sdr.treeflow.loader import (
    TreeFlowLoader,
    TreeFlowNotFoundError,
)

MIN_YAML = """\
id: demo
version: 0.1.0
display_name: Demo
entry_node: saudacao
nodes:
  - id: saudacao
    prompt: Hi.
    exit_condition: { type: all_fields_filled }
    next_nodes:
      - condition: "true"
        target: END
"""


@pytest.fixture
def loader(tmp_path: Path) -> TreeFlowLoader:
    (tmp_path / "tenants" / "t1" / "treeflows").mkdir(parents=True)
    (tmp_path / "tenants" / "t1" / "treeflows" / "demo.yaml").write_text(MIN_YAML)
    return TreeFlowLoader(tenants_dir=tmp_path / "tenants")


def test_load_valid_treeflow(loader: TreeFlowLoader) -> None:
    tf = loader.load("t1", "demo")
    assert tf.id == "demo"
    assert tf.version == "0.1.0"
    assert tf.entry_node == "saudacao"


def test_load_caches_result(loader: TreeFlowLoader) -> None:
    a = loader.load("t1", "demo")
    b = loader.load("t1", "demo")
    assert a is b


def test_load_missing_treeflow_raises(loader: TreeFlowLoader) -> None:
    with pytest.raises(TreeFlowNotFoundError):
        loader.load("t1", "ghost")


def test_reload_bypasses_cache(loader: TreeFlowLoader) -> None:
    a = loader.load("t1", "demo")
    b = loader.reload("t1", "demo")
    assert a is not b
    assert a == b


def test_raw_yaml_returns_source(loader: TreeFlowLoader) -> None:
    raw = loader.raw_yaml("t1", "demo")
    assert "id: demo" in raw
