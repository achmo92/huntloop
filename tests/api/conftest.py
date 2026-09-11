"""Shared API test harness for every Phase 4 API plan.

This file is FINAL: later API plans consume these fixtures and must never
edit them (04-01-PLAN.md). The `client` fixture swaps both session
dependencies via `app.dependency_overrides` so no test ever touches the real
data-dir engines; the `make_session` fixture lets a test body seed rows
directly over the same SQLite file without going through HTTP.

Note: the root `tests/conftest.py` autouse `_env` fixture applies here too —
HUNTLOOP_DATA_DIR/HUNTLOOP_SECRET_KEY are set and both engine caches are
reset around every test.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from huntloop.api.app import create_app
from huntloop.api.deps import get_credentials_session, get_session
from huntloop.credentials.base import (
    CredentialsBase,
    make_credentials_session_factory,
)
from huntloop.db.base import Base, make_engine, make_session_factory


@pytest.fixture
def api_engine(tmp_path) -> Engine:
    """One file-backed SQLite engine per test carrying BOTH schemas:

    the main store (Base.metadata) and the credentials store
    (CredentialsBase.metadata) — separate MetaData objects, no table-name
    collision, one file.
    """
    engine = make_engine(f"sqlite:///{tmp_path / 'api.db'}")
    import huntloop.credentials.models
    import huntloop.db.models  # noqa: F401  (populates Base.metadata)

    Base.metadata.create_all(engine)
    CredentialsBase.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def make_session(api_engine):
    """A plain sessionmaker over api_engine for seeding rows directly in a
    test body (no HTTP). The caller owns closing the session."""
    return make_session_factory(api_engine)


@pytest.fixture
def client(api_engine) -> TestClient:
    app = create_app()
    main_factory = make_session_factory(api_engine)
    credentials_factory = make_credentials_session_factory(api_engine)

    def _override_get_session():
        session = main_factory()
        try:
            yield session
        finally:
            session.close()

    def _override_get_credentials_session():
        session = credentials_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_credentials_session] = _override_get_credentials_session
    with TestClient(app) as test_client:
        yield test_client
