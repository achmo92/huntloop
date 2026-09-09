"""Fail-fast environment configuration.

Every process (CLI, API, scheduler, migration runner) must call
`load_config()` at boot, before touching any database, so a missing or
malformed `HUNTLOOP_SECRET_KEY` is a startup error rather than a lazy
failure the first time a credential is read.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import dotenv
from cryptography.fernet import Fernet


class ConfigError(RuntimeError):
    """Raised for any environment-configuration validation failure."""


@dataclass(frozen=True)
class Config:
    data_dir: Path
    database_url: str
    credentials_database_url: str
    secret_key: str


def load_config() -> Config:
    """Read and validate configuration fresh from `os.environ`.

    Raises ConfigError on any invalid or missing required value. Never
    caches env values at module scope, so tests can monkeypatch env vars
    and observe the change on the next call.
    """
    # override=False: real deploy-time env vars must win over a stray .env file.
    dotenv.load_dotenv(override=False)

    raw_data_dir = os.environ.get("HUNTLOOP_DATA_DIR") or str(Path.cwd() / "data")
    data_dir = Path(raw_data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    database_url = os.environ.get("DATABASE_URL") or f"sqlite:///{data_dir}/huntloop.db"
    if not database_url:
        raise ConfigError("DATABASE_URL must not be empty.")
    if database_url.startswith("postgresql") and not database_url.startswith(
        "postgresql+psycopg://"
    ):
        raise ConfigError(
            "DATABASE_URL must use the 'postgresql+psycopg://' driver prefix "
            "(SQLAlchemy 2.0 + psycopg3 requires this explicit driver token). "
            f"Got: {database_url!r}"
        )

    credentials_database_url = (
        os.environ.get("CREDENTIALS_DATABASE_URL") or f"sqlite:///{data_dir}/credentials.db"
    )
    if not credentials_database_url.startswith("sqlite:"):
        raise ConfigError(
            "CREDENTIALS_DATABASE_URL must always be a sqlite: URL — the "
            "credentials store is never Postgres-backed, regardless of "
            f"DATABASE_URL. Got: {credentials_database_url!r}"
        )

    secret_key = os.environ.get("HUNTLOOP_SECRET_KEY")
    if not secret_key:
        raise ConfigError(
            "HUNTLOOP_SECRET_KEY is required and was not set. Generate one with "
            "`openssl rand -base64 32` and set it as a deploy-time environment "
            "variable. It must never be stored in the data volume."
        )
    try:
        Fernet(secret_key.encode())
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"HUNTLOOP_SECRET_KEY is not a valid Fernet key: {exc}") from exc

    return Config(
        data_dir=data_dir,
        database_url=database_url,
        credentials_database_url=credentials_database_url,
        secret_key=secret_key,
    )


def get_secret_key() -> str:
    """Convenience accessor for callers that need only the validated secret key."""
    return load_config().secret_key
