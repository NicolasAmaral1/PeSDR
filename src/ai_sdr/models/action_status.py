"""Canonical Literal for `action_executions.status` (FE-03c Task 1).

Single source of truth across migration 0028, the ORM column, the worker
job, the repository, and the dispatcher. Keep in sync — if you add a
value here, update migration 0028's upgrade() to extend the CHECK
constraint.

Pattern mirrors ai_sdr.models.talk_status (FE-03b) and
ai_sdr.models.talk_closed_by (FE-03b hotfix).
"""

from __future__ import annotations

from typing import Literal, get_args

ActionStatus = Literal[
    "pending",
    "executing",
    "success",
    "failed",
]

ALL_STATUSES: tuple[str, ...] = get_args(ActionStatus)
