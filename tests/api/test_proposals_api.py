"""LOOP-07 accept and LOOP-09 rejection history on the proposals review API.

Owned by plan 05-04. Consumes ``tests/api/conftest.py`` unedited: ``client``
speaks HTTP against the app with both session dependencies overridden onto a
tmp SQLite file, and ``make_session`` seeds rows directly over that same file.

Per the ``test_jobs_api.py`` convention, nothing from
``huntloop.api.routers.proposals`` is imported at module scope, so the
unimplemented routes fail at request time (404/405) with a real assertion
failure rather than a collection error.

The names ``test_accept_versions_criteria`` and ``test_rejection_history_inline``
are referenced literally by 05-VALIDATION.md and must not change.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from huntloop.criteria.loader import save_new_criteria_version
from huntloop.db.models import CriteriaProposal, ProposalStatus
from tests.loop.factories import default_payload

# The exact key set every list item must carry (LOOP-09's prior_rejections included).
PROPOSAL_KEYS = {
    "id",
    "created_at",
    "status",
    "based_on_run_id",
    "proposed_changes",
    "rationale",
    "evidence",
    "predicted_effect",
    "decided_at",
    "resulting_version",
    "rejection_reason",
    "prior_rejections",
}


# ---------------------------------------------------------------------------
# Seeding helpers (each owns its own session; never touches the client's)
# ---------------------------------------------------------------------------


def _seed_criteria(make_session, payload=None) -> int:
    """Save an active criteria version through the real versioning path."""
    session = make_session()
    try:
        version = save_new_criteria_version(
            session, payload if payload is not None else default_payload()
        )
        session.commit()
        return version
    finally:
        session.close()


def _seed_proposal(
    make_session,
    *,
    proposed_changes: dict,
    status: ProposalStatus = ProposalStatus.PENDING,
    decided_at: datetime | None = None,
    rejection_reason: str | None = None,
    created_at: datetime | None = None,
) -> uuid.UUID:
    session = make_session()
    try:
        proposal = CriteriaProposal(
            proposed_changes=proposed_changes,
            rationale="three fast rejections in a row suggest the age window is too wide",
            status=status,
            decided_at=decided_at,
            rejection_reason=rejection_reason,
        )
        if created_at is not None:
            proposal.created_at = created_at
        session.add(proposal)
        session.commit()
        return proposal.id
    finally:
        session.close()


def _changes(
    field: str = "posting_age_days",
    direction: str = "tighten",
    current_value=30,
    proposed_value=20,
) -> dict:
    return {
        "field": field,
        "direction": direction,
        "current_value": current_value,
        "proposed_value": proposed_value,
    }


def _proposal_state(make_session, proposal_id: uuid.UUID) -> dict:
    session = make_session()
    try:
        row = session.get(CriteriaProposal, proposal_id)
        assert row is not None
        return {
            "status": row.status,
            "decided_at": row.decided_at,
            "rejection_reason": row.rejection_reason,
            "resulting_version": row.resulting_version,
        }
    finally:
        session.close()


def _find(items: list[dict], proposal_id: uuid.UUID) -> dict:
    for item in items:
        if item["id"] == str(proposal_id):
            return item
    raise AssertionError(f"proposal {proposal_id} not present in {items!r}")


# ---------------------------------------------------------------------------
# GET /api/proposals — pending by default, newest first, with LOOP-09 history
# ---------------------------------------------------------------------------


def test_list_defaults_to_pending(client, make_session):
    pending_id = _seed_proposal(make_session, proposed_changes=_changes())
    _seed_proposal(
        make_session,
        proposed_changes=_changes(field="locations"),
        status=ProposalStatus.ACCEPTED,
        decided_at=datetime.now(UTC),
    )

    resp = client.get("/api/proposals")
    assert resp.status_code == 200
    assert [item["id"] for item in resp.json()] == [str(pending_id)]


def test_list_status_filter(client, make_session):
    pending_id = _seed_proposal(make_session, proposed_changes=_changes())
    accepted_id = _seed_proposal(
        make_session,
        proposed_changes=_changes(field="locations"),
        status=ProposalStatus.ACCEPTED,
        decided_at=datetime.now(UTC),
    )
    rejected_id = _seed_proposal(
        make_session,
        proposed_changes=_changes(field="exclusions.employers"),
        status=ProposalStatus.REJECTED,
        decided_at=datetime.now(UTC),
        rejection_reason="not yet",
    )

    everything = client.get("/api/proposals?status=all").json()
    assert {item["id"] for item in everything} == {
        str(pending_id),
        str(accepted_id),
        str(rejected_id),
    }

    rejected_only = client.get("/api/proposals?status=rejected").json()
    assert [item["id"] for item in rejected_only] == [str(rejected_id)]


def test_list_ordering(client, make_session):
    now = datetime.now(UTC)
    older = _seed_proposal(
        make_session,
        proposed_changes=_changes(),
        created_at=now - timedelta(days=2),
    )
    newer = _seed_proposal(
        make_session,
        proposed_changes=_changes(field="locations"),
        created_at=now - timedelta(days=1),
    )

    ids = [item["id"] for item in client.get("/api/proposals").json()]
    assert ids == [str(newer), str(older)]


def test_list_shape(client, make_session):
    _seed_proposal(make_session, proposed_changes=_changes())

    item = client.get("/api/proposals").json()[0]
    assert set(item) == PROPOSAL_KEYS


def test_rejection_history_inline(client, make_session):
    _seed_proposal(
        make_session,
        proposed_changes=_changes(direction="tighten"),
        status=ProposalStatus.REJECTED,
        decided_at=datetime(2026, 1, 1, tzinfo=UTC),
        rejection_reason="Too strict",
    )
    # Same field, different direction -> must contribute nothing.
    _seed_proposal(
        make_session,
        proposed_changes=_changes(direction="loosen"),
        status=ProposalStatus.REJECTED,
        decided_at=datetime(2026, 1, 2, tzinfo=UTC),
        rejection_reason="Wrong direction",
    )
    pending_id = _seed_proposal(
        make_session, proposed_changes=_changes(direction="tighten")
    )

    item = _find(client.get("/api/proposals").json(), pending_id)
    assert len(item["prior_rejections"]) == 1
    assert item["prior_rejections"][0]["rejection_reason"] == "Too strict"
    assert item["prior_rejections"][0]["decided_at"] is not None


def test_prior_rejections_ordered_newest_first(client, make_session):
    _seed_proposal(
        make_session,
        proposed_changes=_changes(),
        status=ProposalStatus.REJECTED,
        decided_at=datetime(2026, 1, 1, tzinfo=UTC),
        rejection_reason="older",
    )
    _seed_proposal(
        make_session,
        proposed_changes=_changes(),
        status=ProposalStatus.REJECTED,
        decided_at=datetime(2026, 1, 2, tzinfo=UTC),
        rejection_reason="newer",
    )
    pending_id = _seed_proposal(make_session, proposed_changes=_changes())

    item = _find(client.get("/api/proposals").json(), pending_id)
    assert [entry["rejection_reason"] for entry in item["prior_rejections"]] == [
        "newer",
        "older",
    ]


# ---------------------------------------------------------------------------
# POST /api/proposals/{id}/accept — LOOP-07
# ---------------------------------------------------------------------------


def test_accept_versions_criteria(client, make_session):
    previous = _seed_criteria(make_session)
    proposal_id = _seed_proposal(
        make_session, proposed_changes=_changes(proposed_value=20)
    )

    resp = client.post(f"/api/proposals/{proposal_id}/accept")
    assert resp.status_code == 200
    version = resp.json()["version"]
    assert version > previous

    current = client.get("/api/criteria").json()["current"]
    assert current["version"] == version
    assert current["payload"]["posting_age_days"] == 20

    accepted = client.get("/api/proposals?status=accepted").json()
    assert [item["id"] for item in accepted] == [str(proposal_id)]


def test_accept_twice_is_409(client, make_session):
    _seed_criteria(make_session)
    proposal_id = _seed_proposal(make_session, proposed_changes=_changes())

    assert client.post(f"/api/proposals/{proposal_id}/accept").status_code == 200
    second = client.post(f"/api/proposals/{proposal_id}/accept")

    assert second.status_code == 409
    assert len(client.get("/api/criteria/versions").json()) == 2


def test_accept_invalid_change_is_422(client, make_session):
    payload = default_payload(seniority_min="senior", seniority_max="staff")
    _seed_criteria(make_session, payload)
    proposal_id = _seed_proposal(
        make_session,
        proposed_changes=_changes(
            field="seniority_max", current_value="staff", proposed_value="junior"
        ),
    )

    resp = client.post(f"/api/proposals/{proposal_id}/accept")
    assert resp.status_code == 422
    assert len(client.get("/api/criteria/versions").json()) == 1


def test_accept_unknown_id_is_404(client):
    resp = client.post(f"/api/proposals/{uuid.uuid4()}/accept")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/proposals/{id}/reject — LOOP-09 retention, optional reason
# ---------------------------------------------------------------------------


def test_reject_records_reason(client, make_session):
    proposal_id = _seed_proposal(make_session, proposed_changes=_changes())

    resp = client.post(
        f"/api/proposals/{proposal_id}/reject", json={"reason": "Too strict"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"

    state = _proposal_state(make_session, proposal_id)
    assert state["status"] == ProposalStatus.REJECTED
    assert state["decided_at"] is not None
    assert state["rejection_reason"] == "Too strict"


def test_reject_without_reason_is_allowed(client, make_session):
    empty_body = _seed_proposal(make_session, proposed_changes=_changes())
    null_reason = _seed_proposal(
        make_session, proposed_changes=_changes(field="locations")
    )
    no_body = _seed_proposal(
        make_session, proposed_changes=_changes(field="exclusions.employers")
    )

    assert client.post(f"/api/proposals/{empty_body}/reject", json={}).status_code == 200
    assert (
        client.post(
            f"/api/proposals/{null_reason}/reject", json={"reason": None}
        ).status_code
        == 200
    )
    assert client.post(f"/api/proposals/{no_body}/reject").status_code == 200

    for proposal_id in (empty_body, null_reason, no_body):
        assert _proposal_state(make_session, proposal_id)["rejection_reason"] is None


def test_reject_twice_is_409(client, make_session):
    proposal_id = _seed_proposal(make_session, proposed_changes=_changes())

    assert (
        client.post(
            f"/api/proposals/{proposal_id}/reject", json={"reason": "no"}
        ).status_code
        == 200
    )
    second = client.post(
        f"/api/proposals/{proposal_id}/reject", json={"reason": "no again"}
    )
    assert second.status_code == 409


def test_reject_unknown_id_is_404(client):
    resp = client.post(f"/api/proposals/{uuid.uuid4()}/reject", json={"reason": "no"})
    assert resp.status_code == 404


def test_reason_is_sanitised(client, make_session):
    proposal_id = _seed_proposal(make_session, proposed_changes=_changes())

    resp = client.post(
        f"/api/proposals/{proposal_id}/reject",
        json={"reason": "<script>alert('x')</script>Too strict"},
    )
    assert resp.status_code == 200

    stored = _proposal_state(make_session, proposal_id)["rejection_reason"]
    assert "Too strict" in stored
    assert "<" not in stored
    assert "script" not in stored.lower()
