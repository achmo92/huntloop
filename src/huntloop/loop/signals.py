"""LOOP-01: classify stored status history into implicit signals.

The adaptive loop's only source of truth about what *happened* to a surfaced
listing is the ``status_events`` table. This module turns that history into one
of four signals per job, with no model call and no user prompt:

``FAST_REJECT``
    ``NEW``/``SHORTLISTED`` -> ``REJECTED`` within :data:`FAST_REJECT_DWELL_DAYS`.
    The score was most likely wrong from the start (D-01). Dwell is measured
    from the transition *immediately preceding* the rejection, falling back to
    ``first_seen_at`` only when the rejection is the job's first event — so a
    job that sat in SHORTLISTED before being rejected is timed from SHORTLISTED.

``LATE_REJECT``
    ``APPLIED``/``INTERVIEWING`` -> ``REJECTED``. The listing looked right on
    paper and failed in practice, which is not necessarily a criteria fault.
    The ``from_status`` decides this; dwell is recorded but never gates it.

``PROGRESSED``
    Reached ``APPLIED`` or beyond. This is confirmation the score was right
    (D-02) — shortlisting alone is deliberately not enough.

``NEUTRAL``
    Everything else, including WITHDRAWN and jobs the user has not acted on.

FAST_REJECT and PROGRESSED are the only signals used as proposal evidence;
LATE_REJECT and NEUTRAL are recorded but never drive a candidate edit.

Nothing here may reach the rubric or the model client: both are outside this
package's import graph (LOOP-08, guarded by ``tests/loop/test_boundaries.py``).
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from huntloop.db.models import Job, JobStatus, StatusEvent
from huntloop.loop.types import FAST_REJECT_DWELL_DAYS, Signal

__all__ = [
    "FAST_REJECT_FROM",
    "LATE_REJECT_FROM",
    "PROGRESSION_DEPTH",
    "PROGRESSION_MIN_DEPTH",
    "JobSignal",
    "classify_transitions",
    "collect_signals",
]

# How far along the pipeline a status is. APPLIED and beyond count as the
# system having been confirmed right (D-02), which is why the threshold below
# is 2 rather than 1.
PROGRESSION_DEPTH: dict[JobStatus, int] = {
    JobStatus.SHORTLISTED: 1,
    JobStatus.APPLIED: 2,
    JobStatus.INTERVIEWING: 3,
    JobStatus.OFFER: 4,
}
PROGRESSION_MIN_DEPTH = 2

# D-01: the stage a rejection comes *from* is what separates "the score was
# wrong" (fast) from "it failed in practice" (late).
FAST_REJECT_FROM: tuple[JobStatus, ...] = (JobStatus.NEW, JobStatus.SHORTLISTED)
LATE_REJECT_FROM: tuple[JobStatus, ...] = (JobStatus.APPLIED, JobStatus.INTERVIEWING)

_SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class JobSignal:
    """One job's classified history — the unit the detector reasons over.

    ``transitions`` is already in the JSON-column shape the evidence payload
    expects: ``[{"from": str | None, "to": str, "changed_at": iso8601}]``.
    ``job_id`` is ``None`` only for the defensive zero-event classification,
    which is unreachable through :func:`collect_signals` (jobs without history
    are not returned at all).
    """

    job_id: UUID | None
    signal: Signal
    dwell_days: float | None
    depth: int
    terminal_status: JobStatus
    transitions: list[dict[str, Any]]


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a timestamp to tz-aware UTC.

    SQLite round-trips can hand back naive datetimes even though every write
    went through ``UTCDateTime``; treating a naive value as UTC keeps dwell
    arithmetic deterministic on both backends.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _dwell_days(rejected_at: datetime, anchor: datetime | None) -> float | None:
    """Whole days between ``anchor`` and the rejection, rounded to 2 places."""
    start = _as_utc(anchor)
    rejected = _as_utc(rejected_at)
    if start is None or rejected is None:
        return None
    return round((rejected - start).total_seconds() / _SECONDS_PER_DAY, 2)


def _serialise_transition(event: StatusEvent) -> dict[str, Any]:
    """Shape one status event for the JSON ``evidence`` column."""
    return {
        "from": event.from_status.value if event.from_status is not None else None,
        "to": event.to_status.value,
        "changed_at": _as_utc(event.changed_at).isoformat(),
    }


def classify_transitions(
    events: Sequence[StatusEvent],
    *,
    job_first_seen_at: datetime | None,
    job_status: JobStatus,
    job_id: UUID | None = None,
) -> JobSignal:
    """Classify one job's ordered ``StatusEvent`` history into a :class:`JobSignal`.

    Precedence is deliberate and first-match-wins: a withdrawal is neutral even
    if the job had progressed; a rejection is judged before progression; and
    only a job that neither withdrew nor was rejected can be PROGRESSED. The
    ``job_id`` keyword exists solely so the zero-event case (which no stored job
    reaches — :func:`collect_signals` never emits one) still produces a signal.
    """
    ordered = sorted(events, key=lambda event: _as_utc(event.changed_at))
    resolved_job_id = job_id if job_id is not None else (ordered[0].job_id if ordered else None)
    transitions = [_serialise_transition(event) for event in ordered]

    def build(signal: Signal, dwell_days: float | None = None, depth: int = 0) -> JobSignal:
        return JobSignal(
            job_id=resolved_job_id,
            signal=signal,
            dwell_days=dwell_days,
            depth=depth,
            terminal_status=job_status,
            transitions=transitions,
        )

    if not ordered:
        return build(Signal.NEUTRAL)

    if job_status is JobStatus.WITHDRAWN:
        return build(Signal.NEUTRAL)

    last = ordered[-1]
    if last.to_status is JobStatus.REJECTED:
        # Dwell is anchored to the transition immediately before the rejection;
        # first_seen_at is the fallback only for a rejection with no predecessor.
        anchor = ordered[-2].changed_at if len(ordered) >= 2 else job_first_seen_at
        dwell_days = _dwell_days(last.changed_at, anchor)
        from_status = last.from_status
        if from_status in LATE_REJECT_FROM:
            return build(Signal.LATE_REJECT, dwell_days)
        if (
            from_status in FAST_REJECT_FROM
            and dwell_days is not None
            and dwell_days <= FAST_REJECT_DWELL_DAYS
        ):
            return build(Signal.FAST_REJECT, dwell_days)
        return build(Signal.NEUTRAL, dwell_days)

    depth = max(PROGRESSION_DEPTH.get(event.to_status, 0) for event in ordered)
    if depth >= PROGRESSION_MIN_DEPTH:
        return build(Signal.PROGRESSED, depth=depth)
    return build(Signal.NEUTRAL, depth=depth)


def collect_signals(
    session: Session,
    *,
    since: datetime | None = None,
    now: datetime | None = None,
) -> list[JobSignal]:
    """Classify every job that has status history, in one grouped query.

    ``since`` filters on the job's *most recent* event, and is applied after
    grouping so a job's full history is still available for classification.
    ``now`` is accepted for the caller's clock symmetry with the rest of the
    loop and is intentionally unused: every rule here is relative to stored
    timestamps, never to wall-clock time.
    """
    del now  # reserved: classification is purely relative to stored timestamps
    statement = (
        select(Job, StatusEvent)
        .join(StatusEvent, StatusEvent.job_id == Job.id)
        .order_by(StatusEvent.job_id, StatusEvent.changed_at)
    )
    rows = session.execute(statement).all()

    signals: list[JobSignal] = []
    for _job_id, group in itertools.groupby(rows, key=lambda row: row[0].id):
        grouped = list(group)
        job = grouped[0][0]
        events = [row[1] for row in grouped]

        if since is not None:
            latest = max(_as_utc(event.changed_at) for event in events)
            if latest < _as_utc(since):
                continue

        signals.append(
            classify_transitions(
                events,
                job_first_seen_at=job.first_seen_at,
                job_status=job.status,
                job_id=job.id,
            )
        )
    return signals
