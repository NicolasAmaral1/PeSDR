import pytest
from pydantic import ValidationError

from ai_sdr.schemas.treeflow_yaml import (
    CollectField,
    ExitCondition,
    NodeSpec,
    TreeFlow,
)

# ---------- minimal happy paths ----------


def test_minimal_treeflow_validates() -> None:
    data = {
        "id": "mentoria",
        "version": "1.0.0",
        "display_name": "Funil Mentoria",
        "entry_node": "saudacao",
        "nodes": [
            {
                "id": "saudacao",
                "prompt": "Diga olá em PT-BR.",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    tf = TreeFlow.model_validate(data)
    assert tf.id == "mentoria"
    assert tf.version == "1.0.0"
    assert tf.entry_node == "saudacao"
    assert tf.nodes[0].id == "saudacao"
    assert tf.nodes[0].exit_condition.type == "all_fields_filled"


def test_node_collects_and_transitions() -> None:
    data = {
        "id": "tf",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "qualif",
        "nodes": [
            {
                "id": "qualif",
                "prompt": "Pergunte X.",
                "collects": [
                    {
                        "field": "faturamento_mensal",
                        "type": "number",
                        "extraction_hint": "valor mensal em R$",
                        "required": True,
                        "validation": {"min": 0},
                    },
                    {"field": "tempo_mercado", "type": "text", "required": True},
                ],
                "exit_condition": {
                    "type": "rule_expression",
                    "expression": "faturamento_mensal != None and tempo_mercado != None",
                },
                "next_nodes": [
                    {"condition": "faturamento_mensal >= 30000", "target": "oferta_premium"},
                    {"condition": "faturamento_mensal < 30000", "target": "oferta_basica"},
                ],
            },
            {
                "id": "oferta_premium",
                "prompt": "Apresente premium.",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            },
            {
                "id": "oferta_basica",
                "prompt": "Apresente básica.",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            },
        ],
    }
    tf = TreeFlow.model_validate(data)
    q = tf.nodes[0]
    assert len(q.collects) == 2
    assert q.collects[0].field == "faturamento_mensal"
    assert q.collects[0].validation == {"min": 0}
    assert len(q.next_nodes) == 2


def test_treeflow_with_followup_and_global_objections() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "follow_up": {
            "enabled": True,
            "max_attempts": 3,
            "sequence": [
                {"after": "24h", "template": "Oi {{nome}}!"},
                {"after": "72h", "template": "Tá aí?"},
            ],
        },
        "global_objections": [
            {"id": "preciso_pensar", "kb": "kb_obj_pensar"},
            {"id": "falta_tempo", "kb": "kb_obj_tempo"},
        ],
        "entry_node": "node_a",
        "nodes": [
            {
                "id": "node_a",
                "prompt": "p",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    tf = TreeFlow.model_validate(data)
    assert tf.follow_up is not None
    assert tf.follow_up.max_attempts == 3
    assert tf.follow_up.sequence[0].after == "24h"
    assert len(tf.global_objections) == 2


# ---------- structural validations ----------


def test_entry_node_must_exist_in_nodes() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "ghost",
        "nodes": [
            {
                "id": "node_a",
                "prompt": "p",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    with pytest.raises(ValidationError, match="entry_node"):
        TreeFlow.model_validate(data)


def test_transition_target_must_exist_or_be_END() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "node_a",
        "nodes": [
            {
                "id": "node_a",
                "prompt": "p",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "missing_node"}],
            }
        ],
    }
    with pytest.raises(ValidationError, match="missing_node"):
        TreeFlow.model_validate(data)


def test_duplicate_node_ids_rejected() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "node_a",
        "nodes": [
            {
                "id": "node_a",
                "prompt": "p",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            },
            {
                "id": "node_a",
                "prompt": "p2",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            },
        ],
    }
    with pytest.raises(ValidationError, match="duplicate"):
        TreeFlow.model_validate(data)


def test_rule_expression_exit_requires_expression_field() -> None:
    with pytest.raises(ValidationError, match="expression"):
        ExitCondition.model_validate({"type": "rule_expression"})


def test_collect_field_type_must_be_known() -> None:
    with pytest.raises(ValidationError):
        CollectField.model_validate({"field": "x", "type": "telepathy"})


def test_node_id_must_be_slug() -> None:
    with pytest.raises(ValidationError):
        NodeSpec.model_validate(
            {
                "id": "Bad ID",
                "prompt": "p",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        )
