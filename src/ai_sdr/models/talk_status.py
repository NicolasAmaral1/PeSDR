"""Canonical Literal for `talks.status` (FE-03b Task 1).

Single source of truth across migration 0026, the ORM column, the worker
scan job, and close_lifecycle module. Keep in sync — if you add a value
here, update migration 0026's upgrade() to extend the CHECK constraint.

Pattern mirrors ai_sdr.models.review_reason (FE-03a) for talks.requires_review_reason.
"""

from __future__ import annotations

from typing import Literal, get_args

TalkStatus = Literal[
    "active",
    "requires_review",
    "closed_completed",  # backward-compat (pre-FE-03b)
    "closed_completed_success",
    "closed_completed_failure",
    "closed_no_interest",
    "closed_duration",
    "closed_inactivity",
    "closed_optout",
    "closed_banned",
]

ALL_STATUSES: tuple[str, ...] = get_args(TalkStatus)
