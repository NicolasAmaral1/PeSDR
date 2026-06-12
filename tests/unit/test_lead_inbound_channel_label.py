"""Lead model exposes inbound_channel_label column — multi-channel pre-paving."""

from __future__ import annotations

from ai_sdr.models.lead import Lead


def test_lead_has_inbound_channel_label_attribute():
    assert hasattr(Lead, "inbound_channel_label")


def test_inbound_channel_label_column_metadata():
    """Column is NOT NULL, TEXT, default 'main' at the table level."""
    col = Lead.__table__.c.inbound_channel_label
    assert col.nullable is False
    assert "TEXT" in str(col.type).upper()
    assert col.server_default is not None
