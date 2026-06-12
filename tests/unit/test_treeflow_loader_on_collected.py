"""TreeFlowLoader parses node.on_collected with validation (FE-03c Task 4)."""

from __future__ import annotations

import logging as _logging

import pytest

from ai_sdr.flowengine.treeflow_loader import (
    OnCollectedAction,
    TreeflowLoadError,
    load_treeflow_v2,
)


_BASE_YAML = """
schema_version: 1
id: test_tf
version: 1.0
sdr_persona:
  voice: ""
  conduct: ""
  examples: []
entry_node: greeting
nodes:
  - id: greeting
    objetivo: "Cumprimentar"
    bridge_instruction: ""
    collects:
      - field: nome
        type: text
        required: true
    exit_condition:
      type: all_fields_filled
    next_nodes:
      - condition: "true"
        target: agendamento_demo
  - id: agendamento_demo
    objetivo: "Agendar"
    bridge_instruction: ""
    collects:
      - field: demo_data
        type: text
        required: true
    exit_condition:
      type: all_fields_filled
    next_nodes: []
    on_collected:
      - field: demo_data
        adapter: logging
        handler: schedule_event
        params:
          title: "Demo {{ collected.nome }}"
          duration_minutes: 30
"""


def test_on_collected_parsed_as_dataclass_list():
    tf = load_treeflow_v2(_BASE_YAML)
    node = tf.nodes["agendamento_demo"]
    assert len(node.on_collected) == 1
    action = node.on_collected[0]
    assert isinstance(action, OnCollectedAction)
    assert action.field == "demo_data"
    assert action.adapter == "logging"
    assert action.handler == "schedule_event"
    assert action.params == {
        "title": "Demo {{ collected.nome }}",
        "duration_minutes": 30,
    }


def test_on_collected_empty_list_when_missing():
    yaml = _BASE_YAML.replace(
        "    on_collected:\n"
        "      - field: demo_data\n"
        "        adapter: logging\n"
        "        handler: schedule_event\n"
        "        params:\n"
        "          title: \"Demo {{ collected.nome }}\"\n"
        "          duration_minutes: 30\n",
        "",
    )
    tf = load_treeflow_v2(yaml)
    node = tf.nodes["agendamento_demo"]
    assert node.on_collected == []


def test_on_collected_field_must_be_in_collects():
    yaml = _BASE_YAML.replace(
        "      - field: demo_data\n        adapter: logging",
        "      - field: ghost\n        adapter: logging",
    )
    with pytest.raises(TreeflowLoadError, match="ghost"):
        load_treeflow_v2(yaml)


def test_on_collected_handler_required():
    yaml = _BASE_YAML.replace(
        "        handler: schedule_event\n",
        "        handler: ''\n",
    )
    with pytest.raises(TreeflowLoadError, match="handler"):
        load_treeflow_v2(yaml)


def test_on_collected_template_syntax_error_is_fatal():
    yaml = _BASE_YAML.replace(
        'title: "Demo {{ collected.nome }}"',
        'title: "Demo {{ unclosed"',
    )
    with pytest.raises(TreeflowLoadError, match="template"):
        load_treeflow_v2(yaml)


def test_on_collected_unknown_adapter_is_warning_not_error(caplog):
    yaml = _BASE_YAML.replace("adapter: logging", "adapter: never_registered")
    with caplog.at_level(_logging.WARNING):
        tf = load_treeflow_v2(yaml)
    node = tf.nodes["agendamento_demo"]
    assert node.on_collected[0].adapter == "never_registered"
    assert any("never_registered" in r.message for r in caplog.records)
