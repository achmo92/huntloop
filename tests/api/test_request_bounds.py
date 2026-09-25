"""Request-bound tests for bulk-input and background fan-out caps (T-04-08).

The registry endpoints accept bulk payloads and queue one background probe per
id. Unbounded, both are a cheap denial-of-service on an unauthenticated
private-network API:

- ``BatchCreate.names`` is capped at 200 and ``ResolveBatchRequest.ids`` at 100
  (Pydantic rejects oversized payloads with 422 before any handler/DB work);
- background resolution probes are concurrency-capped by a process-wide
  semaphore (``HUNTLOOP_MAX_EMPLOYER_CONCURRENCY``), not one unbounded thread
  each.

Consumes tests/api/conftest.py unedited.
"""

from __future__ import annotations

import threading
import time
import uuid

from huntloop.api import background as bg
from huntloop.db.models import Company
from huntloop.registry.resolve import ResolutionResult, ResolutionStatus

# ---------------------------------------------------------------------------
# Bounded bulk payloads -> 422 beyond the cap (T-04-08)
# ---------------------------------------------------------------------------


def test_batch_names_over_cap_is_422(client):
    resp = client.post("/api/companies/batch", json={"names": ["n"] * 201})
    assert resp.status_code == 422


def test_batch_names_at_cap_is_201(client):
    resp = client.post("/api/companies/batch", json={"names": ["n"] * 200})
    assert resp.status_code == 201
    assert len(resp.json()["added"]) == 200


def test_resolve_batch_ids_over_cap_is_422(client, monkeypatch):
    monkeypatch.setattr(
        "huntloop.api.routers.companies.run_in_background", lambda *a, **k: None
    )
    resp = client.post(
        "/api/companies/resolve-batch",
        json={"ids": [str(uuid.uuid4()) for _ in range(101)]},
    )
    assert resp.status_code == 422


def test_resolve_batch_ids_at_cap_is_202(client, make_session, monkeypatch):
    monkeypatch.setattr(
        "huntloop.api.routers.companies.run_in_background", lambda *a, **k: None
    )
    session = make_session()
    try:
        companies = [Company(name=f"At-cap employer {i}") for i in range(100)]
        session.add_all(companies)
        session.flush()
        ids = [str(company.id) for company in companies]
        session.commit()
    finally:
        session.close()

    resp = client.post(
        "/api/companies/resolve-batch",
        json={"ids": ids},
    )
    assert resp.status_code == 202
    assert resp.json() == {"queued": 100}


# ---------------------------------------------------------------------------
# Background probe fan-out is concurrency-capped (T-04-08)
# ---------------------------------------------------------------------------


def test_resolve_batch_fan_out_is_bounded(make_session, api_engine, monkeypatch):
    """N queued probes never exceed the configured concurrency at once.

    The cap is pinned deterministically (``bg._probe_slots``) rather than
    relying on a cached config value. Without the gate, all twelve threads run
    their probe body at the same time and ``max_in_flight`` would be 12.
    """
    session = make_session()
    ids: list[uuid.UUID] = []
    try:
        for i in range(12):
            company = Company(name=f"Bounded Co {i}")
            session.add(company)
            session.flush()
            ids.append(company.id)
        session.commit()
    finally:
        session.close()

    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0

    def _slow_probe(**_kwargs):
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.02)
        with lock:
            in_flight -= 1
        return ResolutionResult(
            status=ResolutionStatus.RESOLVED, platform="greenhouse", slug="acme"
        )

    monkeypatch.setattr(bg, "get_engine", lambda: api_engine)
    monkeypatch.setattr(bg, "resolve_employer", _slow_probe)

    cap = 3
    monkeypatch.setattr(
        bg, "_probe_slots", threading.BoundedSemaphore(cap), raising=False
    )

    threads = [bg.run_in_background(bg.resolve_company_in_background, cid) for cid in ids]
    for thread in threads:
        thread.join(timeout=15)

    assert 1 <= max_in_flight <= cap
