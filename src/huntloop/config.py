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
from dataclasses import dataclass, replace
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
    # GAP-15: how long a RUNNING run may go without a refreshed lease before it
    # is classified stale and reconciled. 8x the heartbeat interval (15s).
    run_stale_after_seconds: int
    # GAP-16: how long a requested stop may go unhonored before the durable
    # sweep finalizes the run STOPPED without waiting for the run thread. 60s
    # equals the LLM client's per-request timeout, so a healthy run gets one
    # full bounded model call to reach its next checkpoint.
    run_stop_grace_seconds: int
    run_at: str
    timezone: str
    # T-04-02: browser-origin / DNS-rebinding boundary. `allowed_hosts` is the
    # hostname allowlist for the web surface (bare IP-literal hosts are always
    # accepted by LANTrustedHostMiddleware); `allowed_origins` is the exact
    # origin escape hatch for proxies that rewrite Host. Both from HUNTLOOP_*.
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    # T-04-04: allow a privately-addressed LLM base URL (e.g. a LAN Ollama).
    # Off by default: the settings API requires https and a public address
    # unless this is set.
    allow_private_endpoint: bool
    run_spend_cap_usd: Decimal | None


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a comma-separated env var into a lowercased tuple of values.

    Unset or blank falls back to ``default`` so an empty string never silently
    disables the entire allowlist.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    values = tuple(value.strip().lower() for value in raw.split(",") if value.strip())
    return values or default


def _bool_env(name: str, default: bool = False) -> bool:
    """Parse a boolean env var; unset or blank falls back to ``default``."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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


def _parse_positive_decimal(raw, *, name: str) -> Decimal:
    """Parse and validate a positive USD decimal, shared by env and Setting rows.

    One validator for both layers: a bad value from `os.environ` and a bad value
    from a hand-edited `Setting` row fail with the same error shape.
    """
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError(
            f"{name} must be a decimal number of US dollars (e.g. 2.00). Got: {raw!r}"
        ) from exc
    if value <= 0:
        raise ConfigError(
            f"{name} must be greater than 0 — unset it entirely to run with no "
            f"spend cap. Got: {value}"
        )
    return value


def _optional_positive_decimal_env(name: str) -> Decimal | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return _parse_positive_decimal(raw, name=name)


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
    run_stale_after_seconds = _positive_int_env("HUNTLOOP_RUN_STALE_AFTER_SECONDS", 120)
    run_stop_grace_seconds = _positive_int_env("HUNTLOOP_RUN_STOP_GRACE_SECONDS", 60)

    run_at = os.environ.get("HUNTLOOP_RUN_AT") or "08:00"
    parse_run_at(run_at)  # fail fast at boot, not when the scheduler builds its trigger
    timezone = _timezone_env("HUNTLOOP_TIMEZONE", "UTC")
    allowed_hosts = _csv_env(
        "HUNTLOOP_ALLOWED_HOSTS", ("localhost", "127.0.0.1", "testserver")
    )
    allowed_origins = _csv_env("HUNTLOOP_ALLOWED_ORIGINS", ())
    allow_private_endpoint = _bool_env("HUNTLOOP_ALLOW_PRIVATE_ENDPOINT")
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
        run_stale_after_seconds=run_stale_after_seconds,
        run_stop_grace_seconds=run_stop_grace_seconds,
        run_at=run_at,
        timezone=timezone,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        allow_private_endpoint=allow_private_endpoint,
        run_spend_cap_usd=run_spend_cap_usd,
    )


# D-15: the seven config keys the settings UI may override at runtime. This
# mapping is the single source of truth — the overlay reads exactly these
# Setting rows and nothing else, so no second config mechanism exists.
SETTING_KEY_TO_FIELD: dict[str, str] = {
    "openai_base_url": "openai_base_url",
    "triage_model": "triage_model",
    "scoring_model": "scoring_model",
    "extraction_model": "extraction_model",
    "run_at": "run_at",
    "timezone": "timezone",
    "run_spend_cap_usd": "run_spend_cap_usd",
}

_MODEL_SETTING_KEYS = ("triage_model", "scoring_model", "extraction_model")


def _validate_stored_setting(key: str, value):
    """Re-validate one stored Setting value using the env path's semantics.

    The settings API validates before writing, but a hand-edited row must still
    fail fast (Phase 1's posture) rather than silently feed a bad value into a
    scheduler trigger or a model call.
    """
    if key == "openai_base_url":
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"Setting '{key}' must be a non-empty URL. Got: {value!r}")
        url = value.strip()
        if not url.startswith(("http://", "https://")):
            raise ConfigError(
                f"Setting '{key}' must be an http:// or https:// URL — all model "
                "access in HuntLoop routes through one OpenAI-compatible endpoint "
                f"(OPS-06). Got: {url!r}"
            )
        return url
    if key in _MODEL_SETTING_KEYS:
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(
                f"Setting '{key}' must be a non-empty model name. Got: {value!r}"
            )
        return value.strip()
    if key == "run_at":
        if not isinstance(value, str):
            raise ConfigError(f"Setting '{key}' must be an HH:MM string. Got: {value!r}")
        parse_run_at(value)  # fail fast with the env parser's message
        return value.strip()
    if key == "timezone":
        if not isinstance(value, str):
            raise ConfigError(
                f"Setting '{key}' must be an IANA timezone name. Got: {value!r}"
            )
        tz_name = value.strip()
        try:
            zoneinfo.ZoneInfo(tz_name)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(
                f"Setting '{key}' must be a valid IANA timezone name (e.g. "
                f"America/New_York, Europe/London, UTC). Got: {value!r} ({exc})"
            ) from exc
        return tz_name
    if key == "run_spend_cap_usd":
        return _parse_positive_decimal(value, name=f"Setting '{key}'")
    raise ConfigError(f"Unknown setting key: {key!r}")


def load_effective_config(session) -> Config:
    """Env boot defaults overlaid by Setting rows (D-15).

    `load_config()` remains the boot-time default layer; the UI's Setting rows
    are the runtime override. A stored-but-null row reads as absent (the
    repository's get_value returns None for both), so clearing the spend cap in
    the UI falls back to the env default — an absent cap by default. Any invalid
    stored value raises ConfigError naming the key, exactly like the env path.
    """
    from huntloop.db.repository import SettingsRepository

    env_cfg = load_config()
    repo = SettingsRepository(session)
    overrides: dict[str, object] = {}
    for key, field in SETTING_KEY_TO_FIELD.items():
        raw = repo.get_value(key)
        if raw is None:
            continue
        overrides[field] = _validate_stored_setting(key, raw)
    if not overrides:
        return env_cfg
    return replace(env_cfg, **overrides)


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
