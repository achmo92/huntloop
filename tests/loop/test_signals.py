"""LOOP-01 signal classification. Owned by plan 05-02.

Every job that carries status history classifies into exactly one of
``FAST_REJECT`` / ``LATE_REJECT`` / ``PROGRESSED`` / ``NEUTRAL``. The
distinction that matters most (D-01) is *stage*, not just outcome: a rejection
that arrives days after a listing first appeared says the score was wrong from
the start, while a rejection after an interview says the listing looked right on
paper and failed in practice. Dwell time is measured from the transition
immediately preceding the rejection — never blindly from ``first_seen_at`` — so
a job that sat in SHORTLISTED before being rejected is timed correctly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from huntloop.db.models import JobStatus
from huntloop.loop.signals import classify_transitions, collect_signals
from huntloop.loop.types import Signal
from tests.loop.factories import (  # noqa: F401  (loop_session is a pytest fixture)
    loop_session,
    make_company,
    make_job,
    make_transitions,
)

FIRST_SEEN = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _job_with_history(session, *, name, title, pairs, start):
    """Insert a job first seen on FIRST_SEEN together with its transitions."""
    company = make_company(session, name=name)
    job = make_job(session, company=company, title=title, first_seen_at=FIRST_SEEN)
    events = make_transitions(session, job, pairs, start=start)
    return job, events


def _classify(job, events):
    return classify_transitions(
        events, job_first_seen_at=job.first_seen_at, job_status=job.status
    )


def test_fast_reject_classification(loop_session):
    """A rejection 3 days after first being seen is a fast reject."""
    job, events = _job_with_history(
        loop_session,
        name="Alpha Corp",
        title="Alpha Quokka",
        pairs=[(JobStatus.NEW, JobStatus.REJECTED)],
        start=FIRST_SEEN + timedelta(days=3),
    )

    result = _classify(job, events)

    assert result.signal is Signal.FAST_REJECT
    assert result.dwell_days == 3.0
    # The transitions are serialised for the evidence JSON column.
    assert result.transitions == [
        {"from": "new", "to": "rejected", "changed_at": (FIRST_SEEN + timedelta(days=3)).isoformat()}
    ]


def test_shortlisted_fast_reject(loop_session):
    """Dwell is measured from the preceding transition, not from first_seen_at."""
    # SHORTLISTED on day 1, REJECTED on day 4 -> 3 days, not 4 from first_seen.
    job, events = _job_with_history(
        loop_session,
        name="Bravo Corp",
        title="Bravo Narwhal",
        pairs=[
            (JobStatus.NEW, JobStatus.SHORTLISTED),
            (JobStatus.SHORTLISTED, JobStatus.REJECTED),
        ],
        start=FIRST_SEEN + timedelta(days=1),
    )

    result = _classify(job, events)

    assert result.signal is Signal.FAST_REJECT
    assert result.dwell_days == 3.0


def test_slow_reject_from_new_is_not_fast(loop_session):
    """Twenty days of sitting in NEW before rejection is too slow to blame the score."""
    job, events = _job_with_history(
        loop_session,
        name="Charlie Corp",
        title="Charlie Pangolin",
        pairs=[(JobStatus.NEW, JobStatus.REJECTED)],
        start=FIRST_SEEN + timedelta(days=20),
    )

    result = _classify(job, events)

    assert result.signal is Signal.NEUTRAL


def test_late_reject_ignores_dwell(loop_session):
    """A rejection after APPLIED is late regardless of how quickly it arrived."""
    job, events = _job_with_history(
        loop_session,
        name="Delta Corp",
        title="Delta Capybara",
        pairs=[(JobStatus.APPLIED, JobStatus.REJECTED)],
        start=FIRST_SEEN + timedelta(days=1),
    )

    result = _classify(job, events)

    assert result.signal is Signal.LATE_REJECT


def test_interviewing_reject_is_late(loop_session):
    """A rejection after INTERVIEWING is late — the listing was good on paper."""
    job, events = _job_with_history(
        loop_session,
        name="Echo Corp",
        title="Echo Axolotl",
        pairs=[(JobStatus.INTERVIEWING, JobStatus.REJECTED)],
        start=FIRST_SEEN + timedelta(days=1),
    )

    result = _classify(job, events)

    assert result.signal is Signal.LATE_REJECT


def test_progression_to_applied(loop_session):
    """Reaching APPLIED (D-02's confirmation floor) is PROGRESSED at depth 2."""
    job, events = _job_with_history(
        loop_session,
        name="Foxtrot Corp",
        title="Foxtrot Meerkat",
        pairs=[
            (None, JobStatus.NEW),
            (JobStatus.NEW, JobStatus.SHORTLISTED),
            (JobStatus.SHORTLISTED, JobStatus.APPLIED),
        ],
        start=FIRST_SEEN,
    )

    result = _classify(job, events)

    assert result.signal is Signal.PROGRESSED
    assert result.depth == 2


def test_offer_is_deepest_progression(loop_session):
    """An offer is the deepest confirmation the score was right."""
    job, events = _job_with_history(
        loop_session,
        name="Golf Corp",
        title="Golf Tapir",
        pairs=[
            (None, JobStatus.NEW),
            (JobStatus.NEW, JobStatus.SHORTLISTED),
            (JobStatus.SHORTLISTED, JobStatus.APPLIED),
            (JobStatus.APPLIED, JobStatus.INTERVIEWING),
            (JobStatus.INTERVIEWING, JobStatus.OFFER),
        ],
        start=FIRST_SEEN,
    )

    result = _classify(job, events)

    assert result.signal is Signal.PROGRESSED
    assert result.depth == 4


def test_shortlisted_only_is_neutral(loop_session):
    """Shortlisting alone is not confirmation — D-02 counts from APPLIED onward."""
    job, events = _job_with_history(
        loop_session,
        name="Hotel Corp",
        title="Hotel Wombat",
        pairs=[
            (None, JobStatus.NEW),
            (JobStatus.NEW, JobStatus.SHORTLISTED),
        ],
        start=FIRST_SEEN,
    )

    result = _classify(job, events)

    assert result.signal is Signal.NEUTRAL


def test_withdrawn_is_neutral(loop_session):
    """A withdrawal is the user's decision, never a criteria signal."""
    job, events = _job_with_history(
        loop_session,
        name="India Corp",
        title="India Vicuna",
        pairs=[
            (None, JobStatus.NEW),
            (JobStatus.NEW, JobStatus.SHORTLISTED),
            (JobStatus.SHORTLISTED, JobStatus.APPLIED),
            (JobStatus.APPLIED, JobStatus.WITHDRAWN),
        ],
        start=FIRST_SEEN,
    )

    result = _classify(job, events)

    assert result.signal is Signal.NEUTRAL


def test_no_events_is_neutral(loop_session):
    """A job with no stored history has no signal and no dwell."""
    company = make_company(loop_session, name="Juliet Corp")
    job = make_job(
        loop_session, company=company, title="Juliet Zebra", first_seen_at=FIRST_SEEN
    )

    result = classify_transitions(
        [], job_first_seen_at=job.first_seen_at, job_status=JobStatus.NEW
    )

    assert result.signal is Signal.NEUTRAL
    assert result.dwell_days is None
    assert result.depth == 0


def test_collect_signals_since_filter(loop_session):
    """``since`` keeps a job only when its most recent event is at or after it."""
    old, _ = _job_with_history(
        loop_session,
        name="Kilo Corp",
        title="Kilo Yak",
        pairs=[(JobStatus.NEW, JobStatus.REJECTED)],
        start=datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
    )
    middle, _ = _job_with_history(
        loop_session,
        name="Lima Corp",
        title="Lima Xenon",
        pairs=[(JobStatus.NEW, JobStatus.APPLIED)],
        start=datetime(2026, 1, 20, 12, 0, tzinfo=UTC),
    )
    newest, _ = _job_with_history(
        loop_session,
        name="Mike Corp",
        title="Mike Wolverine",
        pairs=[(JobStatus.NEW, JobStatus.APPLIED)],
        start=datetime(2026, 1, 30, 12, 0, tzinfo=UTC),
    )

    signals = collect_signals(loop_session, since=datetime(2026, 1, 15, tzinfo=UTC))

    returned = {signal.job_id for signal in signals}
    assert returned == {middle.id, newest.id}
    assert old.id not in returned
