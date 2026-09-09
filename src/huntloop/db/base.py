"""Declarative base and engine/session factories for the MAIN application store.

Synchronous SQLAlchemy only (`create_engine`/`Session`) — see 01-RESEARCH.md
Pattern 5. The async drivers referenced in research/STACK.md are for
LangGraph's own checkpointer, a separate concern from these tables.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from huntloop.config import load_config
from huntloop.sqlite_pragmas import install_sqlite_pragmas

install_sqlite_pragmas()


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    install_sqlite_pragmas()
    return create_engine(url, future=True, echo=False)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False so tests/callers can read attributes off an
    # instance after commit without triggering an implicit reload.
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


_engine_cache: Engine | None = None


def get_engine() -> Engine:
    """Return a process-wide cached Engine built from `load_config().database_url`."""
    global _engine_cache
    if _engine_cache is None:
        _engine_cache = make_engine(load_config().database_url)
    return _engine_cache


def reset_engine_cache() -> None:
    """Clear the cached engine so a repointed DATABASE_URL is picked up fresh."""
    global _engine_cache
    if _engine_cache is not None:
        _engine_cache.dispose()
    _engine_cache = None
