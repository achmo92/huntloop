"""D-06 generation + D-10 dedup. Owned by plan 05-05.

``generate_proposals`` is the tail of a discovery run: it reads stored history
and unconsumed written feedback, gates candidates on their own per-field
threshold (LOOP-04), computes evidence (LOOP-05) and a predicted effect
(LOOP-06), and persists at most one PENDING proposal per field (D-06a).

Three properties are load-bearing here and each has its own test:

- **No model call unless something crossed a threshold or there is unconsumed
  feedback** (D-06). Every test that should not spend money asserts the fake
  client recorded zero calls.
- **Explicit feedback counts toward the gate, never around it** (D-08).
- **Dedup by edit signature** (D-10): a pending proposal is never duplicated, a
  rejected one returns only on strictly stronger evidence, and an accepted one
  never blocks a recurrence.

The fake LLM client is the callable double from ``test_rationale.py``; the
detector is exercised for real except where a test needs a precisely-shaped
candidate that detection's own minimums would never emit.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select

import huntloop.loop.generate as generate_mod
import huntloop.loop.rationale as rationale_mod
from huntloop.db.base import make_session_factory
from huntloop.db.models import (
    Criteria,
    CriteriaProposal,
    FeedbackNote,
    FeedbackSource,
    JobStatus,
    ProposalStatus,
    RunTrigger,
)
from huntloop.db.repository import RunRepository
from huntloop.loop.detect import build_evidence, detect_candidates, gate
from huntloop.loop.generate import generate_proposals
from huntloop.loop.predicted_effect import predict_effect
from huntloop.loop.types import (
    EditCandidate,
    EditDirection,
    Signal,
    build_proposed_changes,
)
from tests.loop.factories import (  # noqa: F401  (loop_session is a pytest fixture)
    default_payload,
    loop_session,
    make_company,
    make_criteria,
    make_job,
    make_transitions,
)
from tests.loop.test_rationale import FakeLlmClient

FIRST_SEEN = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

_FLAG_NAMES = (
    "stretch_role",
    "step_down",
    "language_requirement",
    "location_ambiguity",
    "comp_below_floor",
    "posting_stale",
)

# role_fit is the numerically lowest dimension, so every job scored this way
# attributes to role_fit in Track A (threshold 8 for dimension_weights).
ROLE_FIT_LOW = {
    "role_fit": 1.0,
    "seniority_fit": 4.0,
    "employer_fit": 4.0,
    "trajectory": 4.0,
}


# ---------------------------------------------------------------------------
# Local builders (mirrors test_detect.py's conventions — no shared conftest)
# ---------------------------------------------------------------------------


def _score_flags(*raised: str) -> dict[str, dict]:
    return {name: {"raised": name in raised, "detail": ""} for name in _FLAG_NAMES}


def _fast_reject(session, *, title, company, dimensions=None, flags=None):
    """One job rejected 3 days after first being seen — a FAST_REJECT signal."""
    company_row = make_company(session, name=company)
    job = make_job(
        session,
        company=company_row,
        title=title,
        first_seen_at=FIRST_SEEN,
        score_dimensions=dimensions,
        score_flags=flags,
    )
    make_transitions(
        session,
        job,
        [(JobStatus.NEW, JobStatus.REJECTED)],
        start=FIRST_SEEN + timedelta(days=3),
    )
    return job


def _fast_reject_batch(session, count, *, dimensions=None, flags=None, prefix="A"):
    """``count`` fast rejections with unique titles and companies (no Track C)."""
    return [
        _fast_reject(
            session,
            title=f"Tok{prefix}{index}xyz",
            company=f"Employer {prefix}{index}",
            dimensions=dimensions,
            flags=flags,
        )
        for index in range(count)
    ]


def _proposals(session) -> list[CriteriaProposal]:
    return list(session.execute(select(CriteriaProposal)).scalars())


def _proposal_count(session) -> int:
    return session.execute(select(func.count()).select_from(CriteriaProposal)).scalar_one()


def _add_feedback(
    session,
    text: str,
    *,
    job_id: uuid.UUID | None = None,
    source: FeedbackSource = FeedbackSource.CHAT,
    created_at: datetime | None = None,
) -> FeedbackNote:
    note = FeedbackNote(job_id=job_id, text=text, source=source)
    if created_at is not None:
        note.created_at = created_at
    session.add(note)
    session.flush()
    return note


def _start_run(session) -> uuid.UUID:
    run = RunRepository(session).start(RunTrigger.MANUAL)
    session.flush()
    return run.id


def _claims(*pairs: tuple[str, str]) -> dict:
    """A canned extraction response: ``(quote, field)`` pairs, all tighten."""
    return {
        "claims": [
            {
                "field": field,
                "direction": "tighten",
                "quote": quote,
                "note_index": index,
            }
            for index, (quote, field) in enumerate(pairs)
        ]
    }


def _keyword_candidate(observations: int) -> EditCandidate:
    """A precisely-shaped candidate detection's own minimums would not emit.

    ``exclusions.title_keywords`` has a threshold of 3 (D-05); a real Track C
    candidate always arrives with at least 3 observations, so the only way to
    test that feedback *counts toward* the gate rather than supplying it is to
    hand the pipeline a candidate with fewer.
    """
    return EditCandidate(
        field="exclusions.title_keywords",
        direction=EditDirection.TIGHTEN,
        current_value=[],
        proposed_value=["contract"],
        signal=Signal.FAST_REJECT,
        observations=[
            {
                "job_id": str(uuid.uuid4()),
                "title": f"Contract Engineer {index}",
                "company": f"Employer {index}",
                "status": JobStatus.REJECTED.value,
                "dwell_days": 3.0,
                "transitions": ["new->rejected"],
                "attributed_to": "role_fit",
            }
            for index in range(observations)
        ],
        feedback_quotes=[],
    )


def _patch_detection(monkeypatch, candidates: list[EditCandidate]) -> None:
    monkeypatch.setattr(
        generate_mod,
        "detect_candidates",
        lambda session, *, now=None: list(candidates),
    )


def _rejected_signature_changes() -> dict:
    """The stored ``proposed_changes`` for a (dimension_weights, increase) edit."""
    weights = default_payload().dimension_weights.model_dump()
    return build_proposed_changes(
        "dimension_weights",
        EditDirection.INCREASE,
        weights,
        {**weights, "role_fit": weights["role_fit"] + 1.0},
    )


# ---------------------------------------------------------------------------
# The gate and the cost guard (D-06)
# ---------------------------------------------------------------------------


def test_generate_returns_empty_without_criteria(loop_session):
    """No active criteria version: nothing to generate against, nothing spent."""
    fake = FakeLlmClient()

    assert generate_proposals(loop_session, llm_client=fake) == []
    assert _proposal_count(loop_session) == 0
    assert fake.calls == []


def test_generate_returns_empty_below_threshold(loop_session):
    """3 fast rejects against dimension_weights (threshold 8) spend nothing."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 3, dimensions=ROLE_FIT_LOW)
    fake = FakeLlmClient()

    assert generate_proposals(loop_session, llm_client=fake) == []
    assert _proposal_count(loop_session) == 0
    assert fake.calls == []


def test_generate_creates_one_proposal_per_field(loop_session):
    """D-06a: a backlog crossing two thresholds makes two single-field proposals."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 8, dimensions=ROLE_FIT_LOW)
    _fast_reject_batch(loop_session, 4, flags=_score_flags("stretch_role"), prefix="B")

    ids = generate_proposals(loop_session)

    assert len(ids) == 2
    rows = _proposals(loop_session)
    assert {row.proposed_changes["field"] for row in rows} == {
        "dimension_weights",
        "seniority_max",
    }
    for row in rows:
        assert set(row.proposed_changes) == {
            "field",
            "direction",
            "current_value",
            "proposed_value",
        }


def test_generate_populates_all_columns(loop_session):
    """Every created row is complete and PENDING, stamped with the run it came from."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 8, dimensions=ROLE_FIT_LOW)
    run_id = _start_run(loop_session)

    ids = generate_proposals(loop_session, run_id=run_id)

    assert len(ids) == 1
    row = loop_session.get(CriteriaProposal, ids[0])
    assert row.proposed_changes
    assert row.rationale
    assert row.evidence
    assert row.predicted_effect
    assert row.status is ProposalStatus.PENDING
    assert row.based_on_run_id == run_id


# ---------------------------------------------------------------------------
# D-10 dedup
# ---------------------------------------------------------------------------


def test_generate_is_idempotent_for_pending_signature(loop_session):
    """A second pass over unchanged data creates nothing."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 8, dimensions=ROLE_FIT_LOW)

    first = generate_proposals(loop_session)
    second = generate_proposals(loop_session)

    assert len(first) == 1
    assert second == []
    assert _proposal_count(loop_session) == 1


def test_rejected_signature_returns_only_with_stronger_evidence(loop_session):
    """D-10: 9 observations does not re-show a rejection made at 9; 10 does."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 9, dimensions=ROLE_FIT_LOW)
    loop_session.add(
        CriteriaProposal(
            proposed_changes=_rejected_signature_changes(),
            rationale="you rejected this before",
            evidence={"observation_count": 9},
            predicted_effect={},
            status=ProposalStatus.REJECTED,
        )
    )
    loop_session.commit()

    assert generate_proposals(loop_session) == []
    assert _proposal_count(loop_session) == 1

    _fast_reject_batch(loop_session, 1, dimensions=ROLE_FIT_LOW, prefix="C")

    ids = generate_proposals(loop_session)

    assert len(ids) == 1
    row = loop_session.get(CriteriaProposal, ids[0])
    assert row.status is ProposalStatus.PENDING
    assert row.evidence["observation_count"] == 10


def test_accepted_signature_can_recur(loop_session):
    """An accepted proposal never blocks a later candidate with the same signature."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 8, dimensions=ROLE_FIT_LOW)
    loop_session.add(
        CriteriaProposal(
            proposed_changes=_rejected_signature_changes(),
            rationale="you accepted this before",
            evidence={"observation_count": 8},
            predicted_effect={},
            status=ProposalStatus.ACCEPTED,
        )
    )
    loop_session.commit()

    ids = generate_proposals(loop_session)

    assert len(ids) == 1
    assert loop_session.get(CriteriaProposal, ids[0]).status is ProposalStatus.PENDING


# ---------------------------------------------------------------------------
# D-08: written feedback counts toward the gate, never around it
# ---------------------------------------------------------------------------


def test_feedback_claims_count_toward_threshold(loop_session, monkeypatch):
    """2 observations + 2 matching quotes reach a threshold of 3, evidence carries both."""
    make_criteria(loop_session, default_payload())
    _patch_detection(monkeypatch, [_keyword_candidate(observations=2)])
    first = _add_feedback(loop_session, "stop showing me contract roles")
    second = _add_feedback(loop_session, "no more contract work please")
    fake = FakeLlmClient(
        content=_claims(
            ("stop showing me contract roles", "exclusions.title_keywords"),
            ("no more contract work please", "exclusions.title_keywords"),
        )
    )
    monkeypatch.setattr(rationale_mod, "complete_json", fake)

    ids = generate_proposals(loop_session, llm_client=object())

    assert len(ids) == 1
    evidence = loop_session.get(CriteriaProposal, ids[0]).evidence
    assert evidence["observation_count"] == 4
    assert len(evidence["listings"]) == 2
    assert len(evidence["feedback_quotes"]) == 2
    assert {quote["note_id"] for quote in evidence["feedback_quotes"]} == {
        str(first.id),
        str(second.id),
    }


def test_feedback_alone_still_respects_threshold(loop_session, monkeypatch):
    """2 quotes with zero implicit observations is still short of a threshold of 3."""
    make_criteria(loop_session, default_payload())
    _patch_detection(monkeypatch, [_keyword_candidate(observations=0)])
    _add_feedback(loop_session, "stop showing me contract roles")
    _add_feedback(loop_session, "no more contract work please")
    fake = FakeLlmClient(
        content=_claims(
            ("stop showing me contract roles", "exclusions.title_keywords"),
            ("no more contract work please", "exclusions.title_keywords"),
        )
    )
    monkeypatch.setattr(rationale_mod, "complete_json", fake)

    assert generate_proposals(loop_session, llm_client=object()) == []
    assert _proposal_count(loop_session) == 0


# ---------------------------------------------------------------------------
# Which feedback is read, and how a pass is committed
# ---------------------------------------------------------------------------


def test_only_unconsumed_general_notes_are_read(loop_session, monkeypatch):
    """Only CHAT notes with no listing, newer than the active criteria version."""
    make_criteria(loop_session, default_payload())
    criteria_row = loop_session.execute(select(Criteria)).scalar_one()

    company = make_company(loop_session, name="Feedback Co")
    job = make_job(loop_session, company=company, title="Feedback Role")

    _add_feedback(loop_session, "unconsumed general complaint")
    _add_feedback(
        loop_session,
        "pre-criteria complaint",
        created_at=criteria_row.created_at - timedelta(days=1),
    )
    _add_feedback(loop_session, "per-listing complaint", job_id=job.id)
    _add_feedback(
        loop_session,
        "job-note-sourced complaint",
        source=FeedbackSource.JOB_NOTE,
    )

    fake = FakeLlmClient(content={"claims": []})
    monkeypatch.setattr(rationale_mod, "complete_json", fake)

    generate_proposals(loop_session, llm_client=object())

    assert len(fake.calls) == 1
    prompt = fake.calls[0]["user"]
    assert "unconsumed general complaint" in prompt
    assert "pre-criteria complaint" not in prompt
    assert "per-listing complaint" not in prompt
    assert "job-note-sourced complaint" not in prompt


def test_extraction_skipped_when_no_notes(loop_session, monkeypatch):
    """Zero qualifying notes means no extraction call at all (D-06)."""
    make_criteria(loop_session, default_payload())
    criteria_row = loop_session.execute(select(Criteria)).scalar_one()
    company = make_company(loop_session, name="Quiet Co")
    job = make_job(loop_session, company=company, title="Quiet Role")
    _add_feedback(loop_session, "per-listing only", job_id=job.id)
    _add_feedback(
        loop_session,
        "already consumed",
        created_at=criteria_row.created_at - timedelta(days=2),
    )
    fake = FakeLlmClient()

    generate_proposals(loop_session, llm_client=object())

    assert fake.calls == []


def test_generate_commits_once(loop_session, monkeypatch):
    """One commit per pass, and a failure mid-way leaves zero rows (no partial batch)."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 8, dimensions=ROLE_FIT_LOW)
    _fast_reject_batch(loop_session, 4, flags=_score_flags("stretch_role"), prefix="B")

    commits: list[int] = []
    event.listen(loop_session, "after_commit", lambda *args, **kwargs: commits.append(1))

    ids = generate_proposals(loop_session)

    assert len(ids) == 2
    assert len(commits) == 1

    # Reset the backlog and make the second candidate's persistence explode.
    for proposal in _proposals(loop_session):
        loop_session.delete(proposal)
    loop_session.commit()
    commits.clear()

    real_build_evidence = generate_mod.build_evidence
    calls = {"count": 0}

    def flaky_build_evidence(candidate):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise RuntimeError("evidence computation exploded")
        return real_build_evidence(candidate)

    monkeypatch.setattr(generate_mod, "build_evidence", flaky_build_evidence)

    with pytest.raises(RuntimeError):
        generate_proposals(loop_session)

    assert _proposal_count(loop_session) == 0


def test_evidence_and_predicted_effect_are_json_round_trippable(loop_session):
    """A re-read row returns exactly the dicts that were written."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 8, dimensions=ROLE_FIT_LOW)

    ids = generate_proposals(loop_session)
    row = loop_session.get(CriteriaProposal, ids[0])
    stored_evidence = row.evidence
    stored_effect = row.predicted_effect

    other_factory = make_session_factory(loop_session.get_bind())
    other = other_factory()
    try:
        reloaded = other.get(CriteriaProposal, ids[0])
        assert reloaded.evidence == stored_evidence
        assert reloaded.predicted_effect == stored_effect
        assert json.loads(json.dumps(reloaded.evidence)) == reloaded.evidence
        assert json.loads(json.dumps(reloaded.predicted_effect)) == reloaded.predicted_effect
    finally:
        other.close()

    candidate = next(
        candidate
        for candidate in gate(detect_candidates(loop_session))
        if candidate.field == "dimension_weights"
    )
    assert stored_evidence == build_evidence(candidate)
    assert stored_effect == predict_effect(loop_session, row.proposed_changes)
