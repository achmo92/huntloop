"""Fail-fast environment configuration.

Every process (CLI, API, scheduler, migration runner) must call
`load_config()` at boot, before touching any database, so a missing or
malformed `HUNTLOOP_SECRET_KEY` is a startup error rather than a lazy
failure the first time a credential is read.
"""

from __future__ import annotations

import os
import re
import zoneinfo
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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
    openai_base_url: str
    triage_model: str
    scoring_model: str
    extraction_model: str
    max_employer_concurrency: int
    stale_after_empty_runs: int
    run_at: str
    timezone: str
    run_spend_cap_usd: Decimal | None


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer. Got: {raw!r}") from exc
    if value < 1:
        raise ConfigError(f"{name} must be >= 1. Got: {value}")
    return value


_RUN_AT_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_run_at(value: str) -> tuple[int, int]:
    """Parse a HUNTLOOP_RUN_AT value into (hour, minute).

    Exposed (not private) because huntloop.scheduler.build needs the exact same
    parse to construct its CronTrigger; two parsers would drift.
    """
    match = _RUN_AT_PATTERN.match(value.strip())
    if match is None:
        raise ConfigError(
            "HUNTLOOP_RUN_AT must be a 24-hour HH:MM wall-clock time interpreted "
            f"in HUNTLOOP_TIMEZONE (e.g. 08:00). Got: {value!r}"
        )
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        raise ConfigError(
            "HUNTLOOP_RUN_AT must have hour 00-23 and minute 00-59. "
            f"Got: {value!r}"
        )
    return hour, minute


def _timezone_env(name: str, default: str) -> str:
    raw = os.environ.get(name)
    tz_name = default if raw is None or raw.strip() == "" else raw.strip()
    try:
        zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"{name} must be a valid IANA timezone name (e.g. America/New_York, "
            f"Europe/London, UTC). Got: {tz_name!r} ({exc})"
        ) from exc
    return tz_name


def _optional_positive_decimal_env(name: str) -> Decimal | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise ConfigError(
            f"{name} must be a decimal number of US dollars (e.g. 2.00). Got: {raw!r}"
        ) from exc
    if value <= 0:
        raise ConfigError(
            f"{name} must be greater than 0 — unset it entirely to run with no "
            f"spend cap. Got: {value}"
        )
    return value


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

    openai_base_url = os.environ.get("HUNTLOOP_OPENAI_BASE_URL")
    if openai_base_url is None:
        openai_base_url = "https://api.openai.com/v1"
    openai_base_url = openai_base_url.strip()
    if not openai_base_url:
        raise ConfigError(
            "HUNTLOOP_OPENAI_BASE_URL was set but empty. Unset it to use the "
            "default https://api.openai.com/v1, or set a full base URL."
        )
    if not openai_base_url.startswith(("http://", "https://")):
        raise ConfigError(
            "HUNTLOOP_OPENAI_BASE_URL must be an http:// or https:// URL — all "
            "model access in HuntLoop routes through one OpenAI-compatible "
            f"endpoint (OPS-06). Got: {openai_base_url!r}"
        )

    triage_model = os.environ.get("HUNTLOOP_TRIAGE_MODEL") or "gpt-4o-mini"
    scoring_model = os.environ.get("HUNTLOOP_SCORING_MODEL") or "gpt-4o"
    extraction_model = os.environ.get("HUNTLOOP_EXTRACTION_MODEL") or triage_model

    max_employer_concurrency = _positive_int_env("HUNTLOOP_MAX_EMPLOYER_CONCURRENCY", 5)
    stale_after_empty_runs = _positive_int_env("HUNTLOOP_STALE_AFTER_EMPTY_RUNS", 3)

    run_at = os.environ.get("HUNTLOOP_RUN_AT") or "08:00"
    parse_run_at(run_at)  # fail fast at boot, not when the scheduler builds its trigger
    timezone = _timezone_env("HUNTLOOP_TIMEZONE", "UTC")
    run_spend_cap_usd = _optional_positive_decimal_env("HUNTLOOP_RUN_SPEND_CAP_USD")

    return Config(
        data_dir=data_dir,
        database_url=database_url,
        credentials_database_url=credentials_database_url,
        secret_key=secret_key,
        openai_base_url=openai_base_url,
        triage_model=triage_model,
        scoring_model=scoring_model,
        extraction_model=extraction_model,
        max_employer_concurrency=max_employer_concurrency,
        stale_after_empty_runs=stale_after_empty_runs,
        run_at=run_at,
        timezone=timezone,
        run_spend_cap_usd=run_spend_cap_usd,
    )


def get_secret_key() -> str:
    """Convenience accessor for callers that need only the validated secret key."""
    return load_config().secret_key


def resolve_llm_api_key(credentials_session) -> str:
    """Resolve the OpenAI-compatible API key (OPS-06).

    The credentials store is authoritative — Phase 1 put it in a separate
    Fernet-encrypted SQLite file precisely so a main-DB backup leak can't
    expose it (OPS-05). HUNTLOOP_OPENAI_API_KEY is a builder-only fallback
    for headless CLI use before Phase 4's settings UI exists.
    """
    from huntloop.credentials.store import CredentialStore

    stored = CredentialStore(credentials_session).get("openai_api_key")
    if stored:
        return stored
    env_key = os.environ.get("HUNTLOOP_OPENAI_API_KEY")
    if env_key:
        return env_key
    raise ConfigError(
        "No OpenAI-compatible API key found. Set HUNTLOOP_OPENAI_API_KEY, or "
        "store one under the credential key 'openai_api_key'."
    )
