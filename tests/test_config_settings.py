"""UI-04: the effective-config overlay (env defaults overlaid by Setting rows).

D-15: environment variables stay the boot-time default; a `Setting` row is the
runtime override the settings UI writes. One mechanism, layered — the overlay
re-validates stored values with the SAME validators the env path uses and fails
fast on a hand-edited bad row, exactly like `load_config()`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from huntloop import config
from huntloop.config import ConfigError, load_config
from huntloop.db.repository import SettingsRepository


@pytest.fixture(autouse=True)
def _clean_overlay_env(monkeypatch):
    """Pin the env-default layer so overlay assertions are shell-independent."""
    for name in (
        "HUNTLOOP_OPENAI_BASE_URL",
        "HUNTLOOP_TRIAGE_MODEL",
        "HUNTLOOP_SCORING_MODEL",
        "HUNTLOOP_EXTRACTION_MODEL",
        "HUNTLOOP_RUN_AT",
        "HUNTLOOP_TIMEZONE",
        "HUNTLOOP_RUN_SPEND_CAP_USD",
    ):
        monkeypatch.delenv(name, raising=False)


def _load_effective(session):
    """Call the planned overlay, failing on an assertion if it is not shipped yet.

    `hasattr` + assert rather than a top-level import so the RED run reports a
    real assertion failure for every test instead of one file-level import
    error (TDD RED must fail on the behavior, not on a load crash).
    """
    assert hasattr(config, "load_effective_config"), (
        "huntloop.config.load_effective_config must exist (UI-04 overlay)"
    )
    return config.load_effective_config(session)


def _seed(session, **values):
    repo = SettingsRepository(session)
    for key, value in values.items():
        repo.set_value(key, value)
    session.commit()


# ---------------------------------------------------------------------------
# Mapping contract
# ---------------------------------------------------------------------------


def test_setting_key_mapping_has_exactly_seven_keys():
    assert hasattr(config, "SETTING_KEY_TO_FIELD"), "SETTING_KEY_TO_FIELD must exist"
    assert len(config.SETTING_KEY_TO_FIELD) == 7
    assert set(config.SETTING_KEY_TO_FIELD) == {
        "openai_base_url",
        "triage_model",
        "scoring_model",
        "extraction_model",
        "run_at",
        "timezone",
        "run_spend_cap_usd",
    }


# ---------------------------------------------------------------------------
# Passthrough / overrides
# ---------------------------------------------------------------------------


def test_overlay_no_rows_passthrough(main_session):
    """No Setting rows -> the env Config passes through byte-for-byte."""
    assert _load_effective(main_session) == load_config()


def test_overlay_spend_cap_override(main_session):
    # The settings table stores JSON, so the UI writes a JSON number/string;
    # the overlay still yields a Decimal (the Config field's type).
    _seed(main_session, run_spend_cap_usd="1.50")
    assert _load_effective(main_session).run_spend_cap_usd == Decimal("1.50")


def test_overlay_spend_cap_absent_is_env_default(main_session):
    assert _load_effective(main_session).run_spend_cap_usd is None


def test_overlay_schedule_fields(main_session):
    _seed(main_session, run_at="06:30", timezone="America/New_York")
    cfg = _load_effective(main_session)
    assert cfg.run_at == "06:30"
    assert cfg.timezone == "America/New_York"


def test_overlay_models_and_base_url(main_session):
    _seed(
        main_session,
        openai_base_url="https://llm.example/v1",
        triage_model="triage-x",
        scoring_model="scoring-x",
        extraction_model="extract-x",
    )
    cfg = _load_effective(main_session)
    assert cfg.openai_base_url == "https://llm.example/v1"
    assert cfg.triage_model == "triage-x"
    assert cfg.scoring_model == "scoring-x"
    assert cfg.extraction_model == "extract-x"


def test_overlay_null_spend_cap_row_falls_back_to_env(main_session):
    """A written-but-null row reads as absent -> env default (documented "cleared")."""
    _seed(main_session, run_spend_cap_usd=None)
    assert _load_effective(main_session).run_spend_cap_usd is None


# ---------------------------------------------------------------------------
# Fail-fast on a hand-edited row (writes through the API are validated; the
# overlay must still refuse to run on a bad stored value)
# ---------------------------------------------------------------------------


def test_overlay_invalid_stored_run_at_fails_fast(main_session):
    _seed(main_session, run_at="25:99")
    with pytest.raises(ConfigError):
        _load_effective(main_session)


def test_overlay_invalid_stored_timezone_fails_fast(main_session):
    _seed(main_session, timezone="Mars/Olympus")
    with pytest.raises(ConfigError):
        _load_effective(main_session)


def test_overlay_invalid_stored_base_url_fails_fast(main_session):
    _seed(main_session, openai_base_url="not-a-url")
    with pytest.raises(ConfigError):
        _load_effective(main_session)


def test_overlay_empty_stored_model_fails_fast(main_session):
    _seed(main_session, triage_model="")
    with pytest.raises(ConfigError):
        _load_effective(main_session)


def test_overlay_nonpositive_stored_cap_fails_fast(main_session):
    _seed(main_session, run_spend_cap_usd="-1")
    with pytest.raises(ConfigError):
        _load_effective(main_session)
