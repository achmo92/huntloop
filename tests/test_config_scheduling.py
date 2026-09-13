"""Phase 3 scheduling/spend-cap config contracts (RUN-01, RUN-04, RUN-08)."""

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy
from huntloop.config import ConfigError, load_config, parse_run_at
from huntloop.db.models import Run, RunStatus, RunTrigger


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


class TestRunEnums:
    """RUN-03 / RUN-04 / RUN-08 enum members and their schema neutrality."""

    def test_new_members_exist_with_exact_values(self):
        assert RunStatus.SKIPPED.value == "skipped"
        assert RunStatus.CAPPED.value == "capped"
        # Phase 4 GAP-4: a manual stop is a first-class terminal fact.
        assert RunStatus.STOPPED.value == "stopped"
        assert RunTrigger.CATCH_UP.value == "catch_up"

    def test_rendered_enum_lengths_unchanged(self):
        # SQLAlchemy's non-native Enum renders VARCHAR(max(len(name))). Today's
        # maxima are RUNNING/PARTIAL/SKIPPED/STOPPED=7 and SCHEDULED=9; the new
        # names (SKIPPED=7, CAPPED=6, STOPPED=7, CATCH_UP=8) fit within them, so
        # no column type changes and no migration is required. This guard keeps
        # a future rename from silently requiring one.
        assert sqlalchemy.Enum(RunStatus, native_enum=False).length == 7
        assert sqlalchemy.Enum(RunTrigger, native_enum=False).length == 9

    def test_skipped_and_catch_up_round_trip(self, main_session):
        run = Run(
            id=uuid4(),
            trigger=RunTrigger.CATCH_UP,
            status=RunStatus.SKIPPED,
            started_at=datetime.now(UTC),
        )
        main_session.add(run)
        main_session.flush()
        main_session.expire(run)

        reloaded = main_session.get(Run, run.id)
        assert reloaded.trigger == RunTrigger.CATCH_UP
        assert reloaded.status == RunStatus.SKIPPED
