"""Phase 3 scheduling/spend-cap config contracts (RUN-01, RUN-04, RUN-08)."""

import os
from decimal import Decimal

import pytest
from huntloop.config import ConfigError, load_config, parse_run_at


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """Isolate from a developer's .env: load_config() loads it (by design),
    which would re-supply defaults under test. Mirrors tests/test_config_llm.py."""
    monkeypatch.setattr("huntloop.config.dotenv.load_dotenv", lambda *a, **k: False)


class TestRunAt:
    def test_default_is_08_00(self, monkeypatch):
        monkeypatch.delenv("HUNTLOOP_RUN_AT", raising=False)
        config = load_config()
        assert config.run_at == "08:00"

    def test_eight_colon_zero_zero_parses(self):
        assert parse_run_at("8:00") == (8, 0)

    def test_twenty_three_fifty_nine_parses(self):
        assert parse_run_at("23:59") == (23, 59)

    def test_hour_twenty_five_rejected(self):
        with pytest.raises(ConfigError, match="HUNTLOOP_RUN_AT"):
            parse_run_at("25:00")

    def test_no_colon_rejected(self):
        with pytest.raises(ConfigError, match="HUNTLOOP_RUN_AT"):
            parse_run_at("0800")

    def test_bare_hour_rejected(self):
        with pytest.raises(ConfigError, match="HUNTLOOP_RUN_AT"):
            parse_run_at("8")

    def test_minute_sixty_rejected(self):
        with pytest.raises(ConfigError, match="HUNTLOOP_RUN_AT"):
            parse_run_at("08:60")

    def test_bad_run_at_fails_at_boot(self, monkeypatch):
        monkeypatch.setenv("HUNTLOOP_RUN_AT", "25:00")
        with pytest.raises(ConfigError, match="HUNTLOOP_RUN_AT"):
            load_config()


class TestTimezone:
    def test_default_is_utc(self, monkeypatch):
        monkeypatch.delenv("HUNTLOOP_TIMEZONE", raising=False)
        config = load_config()
        assert config.timezone == "UTC"

    def test_iana_name_accepted_verbatim(self, monkeypatch):
        monkeypatch.setenv("HUNTLOOP_TIMEZONE", "America/New_York")
        config = load_config()
        assert config.timezone == "America/New_York"

    def test_unknown_timezone_names_the_variable(self, monkeypatch):
        monkeypatch.setenv("HUNTLOOP_TIMEZONE", "Mars/Olympus")
        with pytest.raises(ConfigError, match="HUNTLOOP_TIMEZONE"):
            load_config()


class TestRunSpendCapUsd:
    def test_unset_yields_none(self, monkeypatch):
        monkeypatch.delenv("HUNTLOOP_RUN_SPEND_CAP_USD", raising=False)
        config = load_config()
        assert config.run_spend_cap_usd is None

    def test_blank_yields_none(self, monkeypatch):
        monkeypatch.setenv("HUNTLOOP_RUN_SPEND_CAP_USD", "   ")
        config = load_config()
        assert config.run_spend_cap_usd is None

    def test_two_dollars_parses_as_decimal(self, monkeypatch):
        monkeypatch.setenv("HUNTLOOP_RUN_SPEND_CAP_USD", "2.00")
        config = load_config()
        assert config.run_spend_cap_usd == Decimal("2.00")

    def test_zero_rejected(self, monkeypatch):
        monkeypatch.setenv("HUNTLOOP_RUN_SPEND_CAP_USD", "0")
        with pytest.raises(ConfigError, match="HUNTLOOP_RUN_SPEND_CAP_USD"):
            load_config()

    def test_negative_rejected(self, monkeypatch):
        monkeypatch.setenv("HUNTLOOP_RUN_SPEND_CAP_USD", "-1")
        with pytest.raises(ConfigError, match="HUNTLOOP_RUN_SPEND_CAP_USD"):
            load_config()

    def test_non_numeric_rejected(self, monkeypatch):
        monkeypatch.setenv("HUNTLOOP_RUN_SPEND_CAP_USD", "abc")
        with pytest.raises(ConfigError, match="HUNTLOOP_RUN_SPEND_CAP_USD"):
            load_config()
