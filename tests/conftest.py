from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from huntloop.credentials.base import (
    CredentialsBase,
    make_credentials_engine,
    make_credentials_session_factory,
    reset_credentials_engine_cache,
)
from huntloop.db.base import Base, make_engine, make_session_factory, reset_engine_cache


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def data_dir(tmp_path) -> Path:
    d = tmp_path / "data"
    d.mkdir(parents=True)
    return d


@pytest.fixture(autouse=True)
def _env(monkeypatch, data_dir, fernet_key):
    monkeypatch.setenv("HUNTLOOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HUNTLOOP_SECRET_KEY", fernet_key)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CREDENTIALS_DATABASE_URL", raising=False)
    reset_engine_cache()
    reset_credentials_engine_cache()
    yield
    reset_engine_cache()
    reset_credentials_engine_cache()


@pytest.fixture
def main_db_path(data_dir) -> Path:
    return data_dir / "huntloop.db"


@pytest.fixture
def credentials_db_path(data_dir) -> Path:
    return data_dir / "credentials.db"


@pytest.fixture
def main_engine(main_db_path):
    engine = make_engine(f"sqlite:///{main_db_path}")
    import huntloop.db.models  # noqa: F401  (populates Base.metadata)

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def main_session(main_engine):
    factory = make_session_factory(main_engine)
    session = factory()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def credentials_engine(credentials_db_path):
    engine = make_credentials_engine(f"sqlite:///{credentials_db_path}")
    import huntloop.credentials.models  # noqa: F401  (populates CredentialsBase.metadata)

    CredentialsBase.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def credentials_session(credentials_engine):
    factory = make_credentials_session_factory(credentials_engine)
    session = factory()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(scope="session")
def postgres_url():
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"testcontainers not available: {exc}")

    try:
        container = PostgresContainer("postgres:16", driver="psycopg")
        container.start()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Docker unavailable for the Postgres portability leg: {exc}")
        return

    try:
        url = container.get_connection_url()
        if "+psycopg" not in url:
            # Older testcontainers versions may not honor `driver=` uniformly —
            # normalize the driver token ourselves as a fallback.
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
            url = url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
        assert url.startswith("postgresql+psycopg://"), url
        yield url
    finally:
        container.stop()


@pytest.fixture(
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)],
)
def portable_engine(request, main_db_path):
    import huntloop.db.models  # noqa: F401  (populates Base.metadata)

    if request.param == "sqlite":
        engine = make_engine(f"sqlite:///{main_db_path}")
        Base.metadata.create_all(engine)
        yield engine
        engine.dispose()
    else:
        url = request.getfixturevalue("postgres_url")
        engine = make_engine(url)
        Base.metadata.create_all(engine)
        try:
            yield engine
        finally:
            Base.metadata.drop_all(engine)
            engine.dispose()
