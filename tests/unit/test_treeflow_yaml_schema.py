import pytest
from pydantic import ValidationError

from ai_sdr.schemas.treeflow_yaml import (
    CollectField,
    ExitCondition,
    GlobalObjection,
    KBRef,
    NodeObjection,
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
            {
                "id": "preciso_pensar",
                "kb": "kb_obj_pensar",
                "description": "Lead diz que precisa pensar antes de decidir",
            },
            {
                "id": "falta_tempo",
                "kb": "kb_obj_tempo",
                "description": "Lead alega que não tem tempo suficiente para o programa",
            },
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


# ---------- KBRef typing (Plan 3) ----------


def test_node_with_typed_knowledge_base() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "na",
        "nodes": [
            {
                "id": "na",
                "prompt": "p",
                "knowledge_base": [
                    {"id": "kb_oferta", "top_k": 5, "min_score": 0.6},
                    {"id": "kb_obj"},
                ],
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    tf = TreeFlow.model_validate(data)
    kbs = tf.nodes[0].knowledge_base
    assert kbs is not None and len(kbs) == 2
    assert isinstance(kbs[0], KBRef)
    assert kbs[0].id == "kb_oferta"
    assert kbs[0].top_k == 5
    assert kbs[0].min_score == 0.6
    # defaults
    assert kbs[1].id == "kb_obj"
    assert kbs[1].top_k == 3
    assert kbs[1].min_score == 0.7


def test_kbref_top_k_out_of_range_rejected() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "na",
        "nodes": [
            {
                "id": "na",
                "prompt": "p",
                "knowledge_base": [{"id": "kb", "top_k": 0}],
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    with pytest.raises(ValidationError, match="top_k"):
        TreeFlow.model_validate(data)


def test_kbref_min_score_out_of_range_rejected() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "na",
        "nodes": [
            {
                "id": "na",
                "prompt": "p",
                "knowledge_base": [{"id": "kb", "min_score": 1.5}],
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    with pytest.raises(ValidationError, match="min_score"):
        TreeFlow.model_validate(data)


def test_kbref_extra_field_rejected() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "na",
        "nodes": [
            {
                "id": "na",
                "prompt": "p",
                "knowledge_base": [{"id": "kb", "weight": 0.5}],
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    with pytest.raises(ValidationError, match="weight"):
        TreeFlow.model_validate(data)


# ---------- NodeObjection + GlobalObjection (Plan 4a Task 1) ----------


def test_node_objection_requires_description():
    with pytest.raises(ValidationError) as exc:
        NodeObjection(id="preco", kb="kb_obj_preco")
    assert "description" in str(exc.value)


def test_node_objection_description_min_length():
    with pytest.raises(ValidationError):
        NodeObjection(id="preco", kb="kb_obj_preco", description="curto")


def test_node_objection_accepts_as_subnode_optional():
    obj = NodeObjection(
        id="preco",
        kb="kb_obj_preco",
        description="Lead questiona o valor do investimento ou compara com alternativas baratas",
    )
    assert obj.as_subnode is None

    obj2 = NodeObjection(
        id="preco",
        kb="kb_obj_preco",
        description="Lead questiona o valor do investimento ou compara com alternativas baratas",
        as_subnode="obj_preco_node",
    )
    assert obj2.as_subnode == "obj_preco_node"


def test_global_objection_requires_description():
    with pytest.raises(ValidationError) as exc:
        GlobalObjection(id="preco", kb="kb_obj_preco")
    assert "description" in str(exc.value)


def test_node_spec_handles_objections_typed():
    node = NodeSpec(
        id="qualif",
        prompt="x",
        exit_condition={"type": "all_fields_filled"},
        next_nodes=[{"condition": "true", "target": "END"}],
        handles_objections=[
            {
                "id": "preco",
                "kb": "kb_obj_preco",
                "description": "Lead acha que está muito caro ou compara com concorrentes",
            }
        ],
    )
    assert isinstance(node.handles_objections[0], NodeObjection)
    assert node.handles_objections[0].id == "preco"


def test_node_spec_handles_objections_defaults_empty_list():
    node = NodeSpec(
        id="qualif",
        prompt="x",
        exit_condition={"type": "all_fields_filled"},
        next_nodes=[{"condition": "true", "target": "END"}],
    )
    assert node.handles_objections == []


# ---------- description boundary tests (Fix 2) ----------


def test_node_objection_description_boundary_9_chars_fails():
    with pytest.raises(ValidationError):
        NodeObjection(id="x", kb="k", description="1" * 9)


def test_node_objection_description_boundary_10_chars_ok():
    obj = NodeObjection(id="x", kb="k", description="1" * 10)
    assert len(obj.description) == 10


def test_node_objection_description_boundary_300_chars_ok():
    obj = NodeObjection(id="x", kb="k", description="a" * 300)
    assert len(obj.description) == 300


def test_node_objection_description_boundary_301_chars_fails():
    with pytest.raises(ValidationError):
        NodeObjection(id="x", kb="k", description="a" * 301)


# ---------- as_subnode symmetric tests (Fix 3) ----------


def test_global_objection_accepts_as_subnode_optional():
    obj = GlobalObjection(
        id="preco",
        kb="kb_obj_preco",
        description="Lead questiona o valor do investimento ou compara com alternativas",
    )
    assert obj.as_subnode is None

    obj2 = GlobalObjection(
        id="preco",
        kb="kb_obj_preco",
        description="Lead questiona o valor do investimento ou compara com alternativas",
        as_subnode="obj_preco_node",
    )
    assert obj2.as_subnode == "obj_preco_node"


def test_global_objection_as_subnode_rejects_empty_string():
    with pytest.raises(ValidationError):
        GlobalObjection(
            id="preco",
            kb="kb_obj_preco",
            description="Lead questiona o valor do investimento ou compara com alternativas",
            as_subnode="",
        )


def test_node_objection_as_subnode_rejects_empty_string():
    with pytest.raises(ValidationError):
        NodeObjection(
            id="preco",
            kb="kb_obj_preco",
            description="Lead questiona o valor do investimento ou compara com alternativas",
            as_subnode="",
        )


# ---------- Task 2: as_subnode refs + BACK_TO_ORIGIN + objection id uniqueness ----------


def test_as_subnode_must_reference_existing_node():
    with pytest.raises(ValidationError) as exc:
        TreeFlow(
            id="tf",
            version="1.0.0",
            display_name="x",
            entry_node="na",
            nodes=[
                NodeSpec(
                    id="na",
                    prompt="x",
                    exit_condition={"type": "all_fields_filled"},
                    next_nodes=[{"condition": "true", "target": "END"}],
                    handles_objections=[
                        {
                            "id": "preco",
                            "kb": "k",
                            "description": "Lead questiona o valor do investimento sempre",
                            "as_subnode": "nonexistent_node",
                        }
                    ],
                ),
            ],
        )
    assert "nonexistent_node" in str(exc.value)
    assert "as_subnode" in str(exc.value)


def test_global_objection_as_subnode_must_reference_existing_node():
    with pytest.raises(ValidationError):
        TreeFlow(
            id="tf",
            version="1.0.0",
            display_name="x",
            entry_node="na",
            global_objections=[
                {
                    "id": "preco",
                    "kb": "k",
                    "description": "Lead questiona o valor do investimento sempre",
                    "as_subnode": "nonexistent",
                }
            ],
            nodes=[
                NodeSpec(
                    id="na",
                    prompt="x",
                    exit_condition={"type": "all_fields_filled"},
                    next_nodes=[{"condition": "true", "target": "END"}],
                ),
            ],
        )


def test_back_to_origin_accepted_as_transition_target():
    """BACK_TO_ORIGIN is a valid transition target (resolved at runtime)."""
    tf = TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="na",
        nodes=[
            NodeSpec(
                id="na",
                prompt="x",
                exit_condition={"type": "all_fields_filled"},
                next_nodes=[{"condition": "true", "target": "obj_node"}],
                handles_objections=[
                    {
                        "id": "preco",
                        "kb": "k",
                        "description": "Lead questiona o valor do investimento sempre",
                        "as_subnode": "obj_node",
                    }
                ],
            ),
            NodeSpec(
                id="obj_node",
                prompt="x",
                exit_condition={"type": "all_fields_filled"},
                next_nodes=[{"condition": "true", "target": "BACK_TO_ORIGIN"}],
            ),
        ],
    )
    assert tf.nodes[1].next_nodes[0].target == "BACK_TO_ORIGIN"


def test_objection_ids_unique_per_scope():
    """Global and node-local can collide (node-local wins); within a scope they cannot."""
    with pytest.raises(ValidationError):
        NodeSpec(
            id="na",
            prompt="x",
            exit_condition={"type": "all_fields_filled"},
            next_nodes=[{"condition": "true", "target": "END"}],
            handles_objections=[
                {"id": "preco", "kb": "k1", "description": "first dup description here"},
                {"id": "preco", "kb": "k2", "description": "second dup description here"},
            ],
        )

    with pytest.raises(ValidationError):
        TreeFlow(
            id="tf",
            version="1.0.0",
            display_name="x",
            entry_node="na",
            global_objections=[
                {"id": "preco", "kb": "k1", "description": "first dup description here"},
                {"id": "preco", "kb": "k2", "description": "second dup description here"},
            ],
            nodes=[
                NodeSpec(
                    id="na",
                    prompt="x",
                    exit_condition={"type": "all_fields_filled"},
                    next_nodes=[{"condition": "true", "target": "END"}],
                ),
            ],
        )
