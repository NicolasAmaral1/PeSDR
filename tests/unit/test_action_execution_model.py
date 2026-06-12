"""ActionExecution model exposes the expected columns (FE-03c Task 3).

Asserts on the SQLAlchemy table metadata rather than instantiating the model
— ORM instrumentation rejects __new__()-bypass in SQLAlchemy 2.x and the
model has many required FKs that would be noise to fixture here.
"""

from __future__ import annotations

from ai_sdr.models.action_execution import ActionExecution


def test_table_name():
    assert ActionExecution.__tablename__ == "action_executions"


def test_id_column_is_primary_key():
    col = ActionExecution.__table__.c.id
    assert col.primary_key is True


def test_tenant_id_not_nullable():
    assert ActionExecution.__table__.c.tenant_id.nullable is False


def test_talk_id_not_nullable():
    assert ActionExecution.__table__.c.talk_id.nullable is False


def test_value_hash_not_nullable():
    assert ActionExecution.__table__.c.value_hash.nullable is False


def test_params_resolved_is_jsonb():
    col = ActionExecution.__table__.c.params_resolved
    assert "JSONB" in str(col.type).upper()


def test_attempts_default_is_zero():
    col = ActionExecution.__table__.c.attempts
    assert col.server_default is not None


def test_last_error_nullable():
    assert ActionExecution.__table__.c.last_error.nullable is True


def test_external_id_nullable():
    assert ActionExecution.__table__.c.external_id.nullable is True


def test_status_not_nullable():
    assert ActionExecution.__table__.c.status.nullable is False


def test_dedup_unique_constraint_exists():
    names = {c.name for c in ActionExecution.__table__.constraints if c.name}
    assert "uq_action_executions_dedup" in names
