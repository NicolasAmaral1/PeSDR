"""Talk model exposes requires_review_reason column (FE-03a Task 4)."""

from __future__ import annotations

from ai_sdr.models.talk import Talk


def test_talk_has_requires_review_reason_attribute():
    assert hasattr(Talk, "requires_review_reason")


def test_requires_review_reason_column_metadata():
    """Column is nullable, String(64), default None at the table level.

    Asserts on the SQLAlchemy table metadata rather than instantiating Talk —
    Talk has many required FKs that would be noise to fixture here, and ORM
    instrumentation rejects __new__()-bypass access in SQLAlchemy 2.x.
    """
    col = Talk.__table__.c.requires_review_reason
    assert col.nullable is True
    assert col.default is None
    assert "VARCHAR(64)" in str(col.type).upper() or "STRING(64)" in str(col.type).upper()
