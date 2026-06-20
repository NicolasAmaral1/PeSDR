"""Canonical Literal for `talks.requires_review_reason` (FE-03a Task 3).

Single source of truth across producers (escalation, off-topic, validator,
treeflow-missing, objection-exhausted) and the migration / test that
asserts the CHECK constraint. Keep in sync with migration 0025's REASONS
tuple.
"""

from __future__ import annotations

from typing import Literal, get_args

RequiresReviewReason = Literal[
    "escalation_requested",
    "off_topic_exhausted",
    "validator_exhausted",
    "treeflow_version_missing",
    "objection_treatment_exhausted",
    "voice_synthesis_failed",
]

ALL_REASONS: tuple[str, ...] = get_args(RequiresReviewReason)
