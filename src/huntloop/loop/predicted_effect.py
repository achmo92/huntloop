"""LOOP-06 predicted effect: what a proposed criteria change would do (stub).

TDD RED state for plan 05-03 Task 1 — every entry point raises until the
implementation lands.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from huntloop.db.models import Job, JobStatus
from huntloop.discovery.normalize.location import (
    NormalizedLocation,
    normalize_location,  # noqa: F401  (re-exported: job_location delegates here)
)

SAMPLE_CAP = 5
DELTA_EPSILON = Decimal("0.01")
LIVE_STATUSES = (
    JobStatus.NEW,
    JobStatus.SHORTLISTED,
    JobStatus.APPLIED,
    JobStatus.INTERVIEWING,
    JobStatus.OFFER,
)


def load_live_backlog(session) -> list[Job]:
    raise NotImplementedError("owned by plan 05-03 Task 1")


def job_location(job: Job) -> NormalizedLocation:
    raise NotImplementedError("owned by plan 05-03 Task 1")


def predict_filter_change(
    session,
    *,
    field: str,
    current_value: Any,
    proposed_value: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    raise NotImplementedError("owned by plan 05-03 Task 1")


def predict_score_change(
    session,
    *,
    current_value: Any,
    proposed_value: Any,
) -> dict[str, Any]:
    raise NotImplementedError("owned by plan 05-03 Task 1")
