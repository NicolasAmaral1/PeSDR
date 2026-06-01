"""Pure helpers for ai-sdr pilot — no DB, no network, no asyncio."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_sdr.cli.pilot import (
    format_status_line,
    generate_whatsapp_e164,
    resolve_treeflow,
)

# --- generate_whatsapp_e164 ---


def test_generate_whatsapp_e164_format() -> None:
    n = generate_whatsapp_e164()
    assert re.fullmatch(r"\+5511990[0-9a-f]{6}", n), n
    # "+5511990" prefix (8 chars) + 6 hex = 14; matches spec §3 UX example "+5511990ab12cd".
    assert len(n) == 14


def test_generate_whatsapp_e164_is_random() -> None:
    # 100 samples; collision probability negligible (16**6 = 16M combinations)
    samples = {generate_whatsapp_e164() for _ in range(100)}
    assert len(samples) >= 99


# --- resolve_treeflow ---


def test_resolve_treeflow_explicit_flag_wins(tmp_path: Path) -> None:
    # Even if directory has many files, explicit flag is returned as-is.
    (tmp_path / "s" / "treeflows").mkdir(parents=True)
    (tmp_path / "s" / "treeflows" / "a.yaml").write_text("")
    (tmp_path / "s" / "treeflows" / "b.yaml").write_text("")
    assert resolve_treeflow(tmp_path, "s", "explicit") == "explicit"


def test_resolve_treeflow_single_file_auto_pick(tmp_path: Path) -> None:
    (tmp_path / "s" / "treeflows").mkdir(parents=True)
    (tmp_path / "s" / "treeflows" / "qualificacao.yaml").write_text("")
    assert resolve_treeflow(tmp_path, "s", None) == "qualificacao"


def test_resolve_treeflow_no_files_raises(tmp_path: Path) -> None:
    (tmp_path / "s" / "treeflows").mkdir(parents=True)
    with pytest.raises(FileNotFoundError) as exc:
        resolve_treeflow(tmp_path, "s", None)
    assert "treeflows" in str(exc.value)


def test_resolve_treeflow_dir_missing_raises(tmp_path: Path) -> None:
    # tenants/<slug>/treeflows/ does not exist at all
    with pytest.raises(FileNotFoundError):
        resolve_treeflow(tmp_path, "missing-slug", None)


def test_resolve_treeflow_multiple_files_requires_flag(tmp_path: Path) -> None:
    (tmp_path / "s" / "treeflows").mkdir(parents=True)
    (tmp_path / "s" / "treeflows" / "a.yaml").write_text("")
    (tmp_path / "s" / "treeflows" / "b.yaml").write_text("")
    with pytest.raises(ValueError) as exc:
        resolve_treeflow(tmp_path, "s", None)
    msg = str(exc.value)
    assert "a" in msg and "b" in msg
    assert "--treeflow" in msg


# --- format_status_line ---


def test_format_status_line_includes_all_fields() -> None:
    lead = SimpleNamespace(id="2d404cfb-9f60-48c1-b741-9db641f4072e", status="active")
    talkflow = SimpleNamespace(status="active")
    line = format_status_line(lead, talkflow, turn_count=4)
    assert "2d404cfb" in line
    assert "active" in line
    assert "turns=4" in line
