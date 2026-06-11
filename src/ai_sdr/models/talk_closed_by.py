"""Canonical Literal for `talks.closed_by` (FE-03b hotfix).

Single source of truth across migrations (0013 original + 0027 extension),
the ORM column, and the modules that set `closed_by`. Keep in sync — if
you add a value here, update migration 0027's upgrade() to extend the
CHECK constraint.

Pattern mirrors ai_sdr.models.talk_status.
"""

from __future__ import annotations

from typing import Literal, get_args

TalkClosedBy = Literal[
    "rule",
    "optout",
    "llm",
    "operator",
    "sentinel",
    "pipeline_hook",
    "scan_job",
]

ALL_CLOSED_BY: tuple[str, ...] = get_args(TalkClosedBy)
