import json
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
    # Tests control configuration through monkeypatch; never merge a developer's
    # deployment .env into that hermetic environment.
    monkeypatch.setattr("huntloop.config.dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("HUNTLOOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HUNTLOOP_SECRET_KEY", fernet_key)
    # Keep TestClient's conventional host deterministic even when a developer's
    # local .env intentionally narrows the production Host allowlist.
    monkeypatch.setenv(
        "HUNTLOOP_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
    )
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


@pytest.fixture
def jobstore_url(data_dir) -> str:
    """A file-backed SQLite URL for an APScheduler SQLAlchemyJobStore.

    File-backed, not :memory:, because the RUN-04 catch-up tests must build a
    scheduler, stop it, and build a SECOND scheduler over the SAME persisted
    jobstore — an in-memory store would vanish between the two and the test
    would pass for the wrong reason.
    """
    return f"sqlite:///{data_dir / 'jobs.db'}"


@pytest.fixture
def jobstore(jobstore_url):
    """A real SQLAlchemyJobStore over jobstore_url, disposed after the test."""
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

    store = SQLAlchemyJobStore(url=jobstore_url)
    yield store
    try:
        store.shutdown()
    except Exception:  # noqa: BLE001 - store may never have been started
        pass


FIXTURES_DIR = Path(__file__).parent / "fixtures"

def _read_fixture(subdir: str, name: str, suffix: str) -> str:
    path = FIXTURES_DIR / subdir / f"{name}{suffix}"
    if not path.exists():
        raise FileNotFoundError(
            f"Recorded fixture {path} is missing. Fixtures are committed to the repo "
            "and must never be fetched live at test time (02-VALIDATION.md Wave 0)."
        )
    return path.read_text(encoding="utf-8")

@pytest.fixture
def ats_fixture():
    """Load a recorded ATS JSON response by stem, e.g. ats_fixture('lever_empty')."""
    def _load(name: str):
        return json.loads(_read_fixture("ats", name, ".json"))
    return _load

@pytest.fixture
def html_fixture():
    """Load a recorded careers-page HTML document by stem."""
    def _load(name: str) -> str:
        return _read_fixture("html", name, ".html")
    return _load
