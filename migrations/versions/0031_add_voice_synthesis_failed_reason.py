"""extend ck_talks_requires_review_reason to allow 'voice_synthesis_failed' (FE-05)

When voice synthesis fails and fallback_to_text_on_failure is False the
pipeline escalates the Talk with requires_review_reason='voice_synthesis_failed'.
Drops and recreates the CHECK constraint to include the new reason.

Revision ID: 0031_add_voice_synthesis_failed_reason
Revises: 0030_extend_outbound_message_type_audio
Create Date: 2026-06-20 00:00:00
"""

from alembic import op

from ai_sdr.models.review_reason import ALL_REASONS as REASONS

revision = "0031_add_voice_synthesis_failed_reason"
down_revision = "0030_extend_outbound_message_type_audio"
branch_labels = None
depends_on = None

_OLD_REASONS = (
    "escalation_requested",
    "off_topic_exhausted",
    "validator_exhausted",
    "treeflow_version_missing",
    "objection_treatment_exhausted",
)


def upgrade() -> None:
    op.drop_constraint("ck_talks_requires_review_reason", "talks", type_="check")
    op.create_check_constraint(
        "ck_talks_requires_review_reason",
        "talks",
        "requires_review_reason IS NULL OR requires_review_reason IN ("
        + ", ".join(f"'{r}'" for r in REASONS)
        + ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_talks_requires_review_reason", "talks", type_="check")
    op.create_check_constraint(
        "ck_talks_requires_review_reason",
        "talks",
        "requires_review_reason IS NULL OR requires_review_reason IN ("
        + ", ".join(f"'{r}'" for r in _OLD_REASONS)
        + ")",
    )
