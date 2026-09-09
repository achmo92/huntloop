"""Declarative base and engine/session factories for the CREDENTIALS store.

This is a completely separate declarative base with its own MetaData object
from `huntloop.db.base.Base` — this is what makes the two Alembic migration
histories independently versioned. `CredentialsBase` MUST NOT inherit from or
share metadata with `Base`; sharing metadata would make
`alembic --name main_db revision --autogenerate` emit the `credentials` table
into the main store (01-RESEARCH.md Pitfall C).
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from huntloop.config import load_config
from huntloop.sqlite_pragmas import install_sqlite_pragmas

install_sqlite_pragmas()


class CredentialsBase(DeclarativeBase):
    pass


def make_credentials_engine(url: str) -> Engine:
    install_sqlite_pragmas()
    return create_engine(url, future=True, echo=False)


def make_credentials_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


_credentials_engine_cache: Engine | None = None


def get_credentials_engine() -> Engine:
    """Return a process-wide cached Engine built from `load_config().credentials_database_url`."""
    global _credentials_engine_cache
    if _credentials_engine_cache is None:
        _credentials_engine_cache = make_credentials_engine(
            load_config().credentials_database_url
        )
    return _credentials_engine_cache


def reset_credentials_engine_cache() -> None:
    """Clear the cached engine so a repointed CREDENTIALS_DATABASE_URL is picked up fresh."""
    global _credentials_engine_cache
    if _credentials_engine_cache is not None:
        _credentials_engine_cache.dispose()
    _credentials_engine_cache = None
