"""Per-request dependencies for the HuntLoop API (Phase 4).

Mirrors the CLI's engine -> sessionmaker pattern (see
`huntloop.cli.run._make_session_factory`) as FastAPI dependencies, so the API
calls the same domain functions behind HTTP instead of shelling out. Every
session is closed in a `finally` — Phase 1 established that an unclosed
Session leaves a checked-out connection that blocks clean teardown.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import Depends
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from huntloop.credentials.base import (
    get_credentials_engine,
    make_credentials_session_factory,
)
from huntloop.db.base import get_engine, make_session_factory
from huntloop.llm.client import get_llm_client

# ---------------------------------------------------------------------------
# Main application store
# ---------------------------------------------------------------------------

_engine: Engine | None = None
_sessionmaker: sessionmaker[Session] | None = None


def _get_sessionmaker() -> sessionmaker[Session]:
    """Build (and cache) the main-store sessionmaker.

    Cached once at first use, fail-fast: the first request that needs a
    session triggers `load_config()` via `get_engine()`, so a bad
    environment is a request-time boot error — matching the CLI's
    `load_config()`-at-boot semantics.
    """
    global _engine, _sessionmaker
    if _sessionmaker is None:
        _engine = get_engine()
        _sessionmaker = make_session_factory(_engine)
    return _sessionmaker


def get_session() -> Iterator[Session]:
    """Yield a main-store session, always closed when the request finishes."""
    session = _get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Credentials store (separate encrypted SQLite file — OPS-05)
# ---------------------------------------------------------------------------

_credentials_engine: Engine | None = None
_credentials_sessionmaker: sessionmaker[Session] | None = None


def _get_credentials_sessionmaker() -> sessionmaker[Session]:
    """Build (and cache) the credentials-store sessionmaker, mirroring the
    main-store pattern against `huntloop.credentials.base`."""
    global _credentials_engine, _credentials_sessionmaker
    if _credentials_sessionmaker is None:
        _credentials_engine = get_credentials_engine()
        _credentials_sessionmaker = make_credentials_session_factory(_credentials_engine)
    return _credentials_sessionmaker


def get_credentials_session() -> Iterator[Session]:
    """Yield a credentials-store session, always closed when the request finishes."""
    session = _get_credentials_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# LLM client (single OpenAI-compatible interface — OPS-06)
# ---------------------------------------------------------------------------


def get_llm(
    credentials_session: Session = Depends(get_credentials_session),
) -> Any:
    """Return the LLM client for this request.

    An `openai.OpenAI` instance built by `huntloop.llm.client.get_llm_client`.
    Deliberately duck-typed, NOT `-> openai.OpenAI`: OPS-06 confines the
    openai import to huntloop/llm/client.py alone (enforced by
    tests/scoring/test_client_routing.py), so this dependency stays
    import-free and tests override it with fakes via
    `app.dependency_overrides[get_llm]`, exactly like the session
    dependencies above.
    """
    return get_llm_client(credentials_session)
