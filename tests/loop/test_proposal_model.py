"""LOOP-09 retention: ``CriteriaProposal.rejection_reason`` column.

Owned by plan 05-01. Sessions are built from ``Base.metadata.create_all`` over
a tmp-path SQLite file via the root ``tests/conftest.py`` fixtures — this is a
model-level test and deliberately does not touch ``tests/api/conftest.py``,
which is FINAL and API-scoped.
"""

from sqlalchemy import select

from huntloop.db.base import make_session_factory
from huntloop.db.models import CriteriaProposal, ProposalStatus


def _proposal(**overrides) -> CriteriaProposal:
    values = {
        "proposed_changes": {
            "field": "posting_age_days",
            "direction": "tighten",
            "current_value": 30,
            "proposed_value": 20,
        },
        "rationale": "stale reposts keep getting rejected",
        "status": ProposalStatus.REJECTED,
    }
    values.update(overrides)
    return CriteriaProposal(**values)


def test_rejection_reason_defaults_to_null(main_session):
    proposal = _proposal()
    main_session.add(proposal)
    main_session.commit()

    reloaded = main_session.execute(
        select(CriteriaProposal).where(CriteriaProposal.id == proposal.id)
    ).scalar_one()

    assert reloaded.rejection_reason is None


def test_rejection_reason_round_trips(main_engine):
    reason = "Too aggressive on seniority"
    factory = make_session_factory(main_engine)

    with factory() as session:
        proposal = _proposal(rejection_reason=reason)
        session.add(proposal)
        session.commit()
        proposal_id = proposal.id

    # A brand-new session must read back exactly what was written.
    with factory() as session:
        reloaded = session.get(CriteriaProposal, proposal_id)
        assert reloaded is not None
        assert reloaded.rejection_reason == reason


def test_rejection_reason_accepts_long_text(main_session):
    # 2000 characters proves the column is unbounded Text, not a bounded String.
    long_reason = "because " * 250
    assert len(long_reason) == 2000

    proposal = _proposal(rejection_reason=long_reason)
    main_session.add(proposal)
    main_session.commit()
    main_session.expire_all()

    reloaded = main_session.get(CriteriaProposal, proposal.id)
    assert reloaded.rejection_reason == long_reason
    assert len(reloaded.rejection_reason) == 2000
