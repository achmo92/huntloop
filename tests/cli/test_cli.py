import decimal
import json
import pytest
from pathlib import Path
from huntloop.cli.main import main, EXIT_OK, EXIT_PARTIAL, EXIT_ABORTED
from huntloop.registry.resolve import ResolutionResult, ResolutionStatus
from huntloop.registry.signatures import SlugCandidate
from huntloop.config import ConfigError
from huntloop.db.models import Company

class DummyClient:
    def __init__(self, *args, **kwargs):
        pass

@pytest.fixture(autouse=True)
def _stub_client(monkeypatch):
    monkeypatch.setattr("huntloop.cli.company._make_client", lambda: DummyClient())

def mock_resolve_employer(*args, **kwargs):
    name = kwargs.get("name")
    if name == "Resolved":
        return ResolutionResult(status=ResolutionStatus.RESOLVED, platform="lever", slug="resolved")
    elif name == "Unresolved":
        return ResolutionResult(status=ResolutionStatus.UNRESOLVED)
    elif name == "Ambiguous":
        return ResolutionResult(
            status=ResolutionStatus.AMBIGUOUS,
            candidates=(
                SlugCandidate(platform="lever", slug="a", source="name_guess"),
                SlugCandidate(platform="greenhouse", slug="b", source="name_guess")
            )
        )
    return ResolutionResult(status=ResolutionStatus.RESOLVED, platform="lever", slug="acme")

@pytest.fixture(autouse=True)
def _stub_resolve(monkeypatch):
    monkeypatch.setattr("huntloop.cli.company.resolve_employer", mock_resolve_employer)

class TestCompanyCommands:
    def test_help(self, capsys):
        assert main([]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "usage: huntloop" in out

    def test_version(self, capsys):
        try:
            main(["--version"])
        except SystemExit as exc:
            assert exc.code == EXIT_OK
        out, _ = capsys.readouterr()
        assert "huntloop" in out
        
    def test_add_url(self, capsys, main_session):
        assert main(["company", "add", "Acme", "--url", "https://acme.com/careers"]) == EXIT_OK
        assert main_session.query(Company).filter_by(name="Acme").count() == 1
        
    def test_add_name_only(self, capsys, main_session):
        assert main(["company", "add", "Acme"]) == EXIT_OK
        assert main_session.query(Company).filter_by(name="Acme").count() == 1
        
    def test_add_resolved(self, capsys, main_session):
        assert main(["company", "add", "Resolved"]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "[Resolved] RESOLVED: lever (resolved)" in out
        
    def test_add_unresolved(self, capsys, main_session):
        assert main(["company", "add", "Unresolved"]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "[Unresolved] registered, not resolved" in out
        
    def test_add_ambiguous(self, capsys, main_session):
        assert main(["company", "add", "Ambiguous"]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "[Ambiguous] AMBIGUOUS" in out
        assert "lever(a)" in out
        
    def test_add_twice_updates(self, capsys, main_session):
        assert main(["company", "add", "Acme"]) == EXIT_OK
        assert main(["company", "add", "Acme"]) == EXIT_OK
        comps = main_session.query(Company).filter_by(name="Acme").all()
        assert len(comps) == 1
        
    def test_import_list(self, capsys, main_session, tmp_path):
        import_file = tmp_path / "companies.yml"
        import_file.write_text("- Acme\n- name: Resolved\n  url: https://resolved.com\n- 123")
        
        assert main(["company", "import", str(import_file)]) == EXIT_PARTIAL
        out, err = capsys.readouterr()
        
        assert "malformed entry" in err
        assert main_session.query(Company).filter_by(name="Acme").count() == 1
        assert main_session.query(Company).filter_by(name="Resolved").count() == 1
        
    def test_import_malformed_top_level(self, capsys, tmp_path, main_session):
        import_file = tmp_path / "companies.yml"
        import_file.write_text("name: Acme")
        
        assert main(["company", "import", str(import_file)]) == EXIT_ABORTED
        out, err = capsys.readouterr()
        assert "expected a top-level list" in err
        
    def test_resolve_existing(self, capsys, main_session):
        main(["company", "add", "Resolved"])
        assert main(["company", "resolve", "Resolved"]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "[Resolved] RESOLVED:" in out
        
    def test_resolve_unknown(self, capsys, main_session):
        assert main(["company", "resolve", "Unknown"]) == EXIT_ABORTED
        out, err = capsys.readouterr()
        assert "unknown employer" in err
        
    def test_list(self, capsys, main_session):
        main(["company", "add", "Resolved"])
        main(["company", "add", "Unresolved"])
        capsys.readouterr()  # clear buffer
        
        assert main(["company", "list"]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "Resolved" in out
        assert "Unresolved" in out
        # check secrets
        assert "api_key" not in out
        assert "secret" not in out
        
    def test_list_json(self, capsys, main_session):
        main(["company", "add", "Resolved"])
        capsys.readouterr()  # clear buffer
        
        assert main(["company", "list", "--json"]) == EXIT_OK
        out, _ = capsys.readouterr()
        data = json.loads(out)
        assert len(data) >= 1
        assert "name" in data[0]
        
    def test_config_error_caught(self, capsys, monkeypatch, main_session):
        def raise_config_error(*args, **kwargs):
            raise ConfigError("Test error")
        monkeypatch.setattr("huntloop.cli.company._make_session", raise_config_error)
        
        assert main(["company", "list"]) == EXIT_ABORTED
        out, err = capsys.readouterr()
        assert "error: Test error" in err

class TestCriteriaCommand:
    def test_load_creates_version(self, capsys, main_session, tmp_path):
        import yaml
        payload = {
            "profile_summary": "Test profile",
            "dimension_weights": {
                "role_fit": 1.0,
                "seniority_fit": 1.0,
                "employer_fit": 1.0,
                "trajectory": 1.0
            }
        }
        yaml_file = tmp_path / "criteria.yml"
        with open(yaml_file, "w") as f:
            yaml.dump(payload, f)
            
        assert main(["criteria", "load", str(yaml_file)]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "version 1" in out
        
        # load again creates version 2
        assert main(["criteria", "load", str(yaml_file)]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "version 2" in out
        
        from huntloop.db.models import Criteria
        versions = main_session.query(Criteria).order_by(Criteria.version).all()
        assert len(versions) == 2
        assert not versions[0].is_active
        assert versions[1].is_active

    def test_load_invalid_yaml(self, capsys, tmp_path):
        yaml_file = tmp_path / "criteria.yml"
        yaml_file.write_text("dimension_weights: 123")
        assert main(["criteria", "load", str(yaml_file)]) == EXIT_ABORTED
        out, err = capsys.readouterr()
        assert "validation failed" in err

    def test_load_nonexistent(self, capsys):
        assert main(["criteria", "load", "nonexistent.yml"]) == EXIT_ABORTED
        out, err = capsys.readouterr()
        assert "nonexistent.yml not found" in err

class TestRunCommand:
    @pytest.fixture
    def mock_run_discovery(self, monkeypatch):
        class MockSummary:
            run_id = "test-run"
            status = "completed"
            tokens_in = 100
            tokens_out = 200
            cost_usd = 0.05
            companies_checked = 1
            listings_fetched = 5
            after_dedup = 4
            after_deterministic = 3
            after_triage = 2
            scored = 2
            new_jobs_written = 1
            updated = 0
            failed = 0
            errors = []
            top_listings = []
            
        summary = MockSummary()
        def _mock(*args, **kwargs):
            return summary
        monkeypatch.setattr("huntloop.cli.run.run_discovery", _mock)
        return summary
        
    def test_run_clean(self, capsys, mock_run_discovery):
        assert main(["run"]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "Run ID: test-run" in out
        assert "fetched: 5" in out
        
    def test_run_partial(self, capsys, mock_run_discovery):
        mock_run_discovery.errors = [{"company": "Test", "stage": "fetch", "message": "boom"}]
        assert main(["run"]) == EXIT_PARTIAL
        out, _ = capsys.readouterr()
        assert "[Test] fetch: boom" in out

    def test_run_limit_with_frozen_summary(self, capsys, monkeypatch):
        """--limit must not crash on the FROZEN RunSummary (found live at the
        02-12 checkpoint: --limit defaults to 10, so every run aborted with
        "cannot assign to field 'top_listings'")."""
        from huntloop.graph.build import RunSummary

        summary = RunSummary(
            run_id="test-run",
            companies_checked=1, listings_fetched=2, after_dedup=2,
            after_deterministic=2, after_triage=2, scored=2,
            new_jobs_written=2, updated=0, failed=0,
            tokens_in=10, tokens_out=20, cost_usd=None,
            errors=(), status="success",
            top_listings=(
                {"title": "A", "url": "https://x/1", "score": 4.5, "company_name": "Co"},
                {"title": "B", "url": "https://x/2", "score": 3.5, "company_name": "Co"},
            ),
        )
        monkeypatch.setattr("huntloop.cli.run.run_discovery", lambda *a, **k: summary)
        assert main(["run", "--limit", "1"]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "A" in out
        assert "B" not in out
        
    def test_run_aborted(self, capsys, monkeypatch):
        def _mock(*args, **kwargs):
            raise Exception("Fatal error")
        monkeypatch.setattr("huntloop.cli.run.run_discovery", _mock)
        assert main(["run"]) == EXIT_ABORTED
        
    def test_run_no_active_criteria(self, capsys, monkeypatch):
        def _mock(*args, **kwargs):
            raise NoActiveCriteria("no active criteria")
        monkeypatch.setattr("huntloop.cli.run.run_discovery", _mock)
        assert main(["run"]) == EXIT_ABORTED
        
    def test_run_no_score_flag(self, capsys, monkeypatch):
        called_with_no_score = False
        def _mock(*args, **kwargs):
            nonlocal called_with_no_score
            called_with_no_score = kwargs.get("no_score", False)
            class MockSummary:
                run_id = "test"
                status = "completed"
                tokens_in = 0
                tokens_out = 0
                cost_usd = 0
                companies_checked = 0
                listings_fetched = 0
                after_dedup = 0
                after_deterministic = 0
                after_triage = 0
                scored = 0
                new_jobs_written = 0
                updated = 0
                failed = 0
                errors = []
                top_listings = []
            return MockSummary()
        monkeypatch.setattr("huntloop.cli.run.run_discovery", _mock)
        assert main(["run", "--no-score"]) == EXIT_OK
        assert called_with_no_score is True
        
    def test_run_json(self, capsys, mock_run_discovery):
        import decimal
        mock_run_discovery.cost_usd = decimal.Decimal("10.50")
        assert main(["run", "--json"]) == EXIT_OK
        out, _ = capsys.readouterr()
        data = json.loads(out)
        
        from huntloop.cli.render import RUN_FIELDS
        for attr, _ in RUN_FIELDS:
            assert attr in data
        assert data["cost_usd"] == 10.50

class TestRunHistoryRender:
    """03-05 Task 1: render_run_history_human / render_run_history_json.

    Renderer-only tests -- plain SimpleNamespace rows, no DB. The imports are
    deliberately inside the methods until the renderers exist (TDD RED keeps
    the rest of the file collectable).
    """

    def _run(self, **overrides):
        from datetime import datetime, timezone
        from types import SimpleNamespace

        from huntloop.db.models import RunStatus, RunTrigger

        base = dict(
            id="00000000-0000-0000-0000-000000000001",
            started_at=datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 9, 10, 8, 5, tzinfo=timezone.utc),
            trigger=RunTrigger.SCHEDULED,
            status=RunStatus.SUCCESS,
            companies_checked=2,
            listings_fetched=10,
            after_dedup=9,
            after_deterministic=7,
            after_triage=5,
            scored=5,
            new_jobs_written=3,
            tokens_in=1200,
            tokens_out=2400,
            cost_usd=decimal.Decimal("0.0123"),
            error_summary=None,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_run_history_empty_message(self):
        from huntloop.cli.render import render_run_history_human

        out = render_run_history_human([])
        assert "no runs recorded" in out
        # An empty history states the fact -- it must not print an empty table.
        assert out.count("\n") == 0

    def test_run_history_human_shows_trigger_status_and_cost(self):
        from huntloop.cli.render import render_run_history_human

        out = render_run_history_human([self._run()])
        assert "2026-09-10 08:00" in out
        assert "scheduled" in out
        assert "success" in out
        assert "written=3" in out
        assert "$0.0123" in out

    def test_run_history_human_null_cost_renders_zero(self):
        from huntloop.cli.render import render_run_history_human

        out = render_run_history_human([self._run(cost_usd=None)])
        assert "$0.0000" in out

    def test_run_history_human_shows_skip_reason(self):
        from huntloop.cli.render import render_run_history_human

        from huntloop.db.models import RunStatus

        run = self._run(
            status=RunStatus.SKIPPED,
            error_summary="skipped: a previous run was still in progress\nsecond line",
        )
        out = render_run_history_human([run])
        assert "skipped" in out
        assert "skipped: a previous run was still in progress" in out

    def test_run_history_json_matches_field_list(self):
        from huntloop.cli.render import RUN_HISTORY_FIELDS, render_run_history_json

        out = render_run_history_json([self._run()])
        data = json.loads(out)
        assert isinstance(data, list) and len(data) == 1
        field_names = {name for name, _ in RUN_HISTORY_FIELDS}
        assert field_names <= set(data[0].keys())

    def test_run_history_json_cost_has_no_float_artifact(self):
        from huntloop.cli.render import render_run_history_json

        out = render_run_history_json([self._run(cost_usd=decimal.Decimal("0.0123"))])
        # Assert on the raw string: the whole point is that the serialized
        # number never shows a float-repr artifact.
        assert "0.0123" in out
        assert "0.012300000000000001" not in out


class TestRunHistoryCommand:
    """03-05 Task 2: `huntloop run history` must work without breaking bare
    `huntloop run` (Phase 2's dispatch must survive the new sub-subparser)."""

    def _seed_and_patch(self, main_engine, monkeypatch):
        """Seed two finished Run rows, then point the CLI at this test engine."""
        from huntloop.db.base import make_session_factory
        from huntloop.db.models import RunStatus, RunTrigger
        from huntloop.db.repository import RunRepository

        factory = make_session_factory(main_engine)
        session = factory()
        try:
            repo = RunRepository(session)
            run1 = repo.start(RunTrigger.SCHEDULED)
            repo.finish(
                run1.id,
                status=RunStatus.SUCCESS,
                companies_checked=2, listings_fetched=10, after_dedup=9,
                after_deterministic=7, after_triage=5, scored=5,
                new_jobs_written=3, tokens_in=1200, tokens_out=2400,
                cost_usd=decimal.Decimal("0.0123"),
            )
            run2 = repo.start(RunTrigger.MANUAL)
            repo.finish(
                run2.id,
                status=RunStatus.PARTIAL,
                companies_checked=1, listings_fetched=4, after_dedup=4,
                after_deterministic=4, after_triage=2, scored=2,
                new_jobs_written=1, tokens_in=500, tokens_out=900,
                cost_usd=decimal.Decimal("0.4567"),
                error_summary="[X] fetch: boom",
            )
            session.commit()
        finally:
            session.close()
        monkeypatch.setattr("huntloop.cli.run._make_session_factory", lambda: factory)
        return factory

    def test_run_history_human_output(self, capsys, main_engine, monkeypatch):
        self._seed_and_patch(main_engine, monkeypatch)
        assert main(["run", "history"]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "scheduled" in out
        assert "manual" in out
        assert "success" in out
        assert "partial" in out
        assert "$0.0123" in out
        assert "$0.4567" in out

    def test_run_history_json_output(self, capsys, main_engine, monkeypatch):
        self._seed_and_patch(main_engine, monkeypatch)
        assert main(["run", "history", "--json"]) == EXIT_OK
        out, _ = capsys.readouterr()
        data = json.loads(out)
        assert len(data) == 2
        assert data[0]["run_id"]
        assert "cost_usd" in data[0]
        assert "status" in data[0]

    def test_run_history_limit_is_passed(self, capsys, main_engine, monkeypatch):
        from huntloop.db.base import make_session_factory
        from huntloop.db.repository import RunRepository

        factory = make_session_factory(main_engine)
        monkeypatch.setattr("huntloop.cli.run._make_session_factory", lambda: factory)
        seen = {}

        def _record(self_repo, limit=10):
            seen["limit"] = limit
            return []

        monkeypatch.setattr(RunRepository, "list_recent", _record)
        assert main(["run", "history", "--limit", "3"]) == EXIT_OK
        assert seen["limit"] == 3

    def test_bare_run_still_dispatches_to_cmd_run(self, capsys, monkeypatch):
        """The regression this task most risks: adding a `run` sub-subparser
        with required=True would break every existing bare `huntloop run`."""
        called = {}

        def _fake_cmd_run(args):
            called["args"] = args
            return EXIT_OK

        monkeypatch.setattr("huntloop.cli.main.cmd_run", _fake_cmd_run)
        assert main(["run", "--no-score"]) == EXIT_OK
        assert "args" in called, "bare `huntloop run` must still reach cmd_run"
        assert called["args"].no_score is True

    def test_run_history_empty_db_exits_zero(self, capsys, main_engine, monkeypatch):
        from huntloop.db.base import make_session_factory

        factory = make_session_factory(main_engine)
        monkeypatch.setattr("huntloop.cli.run._make_session_factory", lambda: factory)
        assert main(["run", "history"]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "no runs recorded" in out


def test_run_history_human_output(capsys, main_engine, monkeypatch):
    """Module-level alias so BOTH node IDs resolve:

    - tests/cli/test_cli.py::test_run_history_human_output -- the exact node ID
      named in 03-VALIDATION.md's per-task verification map. pytest does not
      resolve class-scoped tests through a class-less node ID (verified live),
      so the validation contract's command needs this module-level function.
    - tests/cli/test_cli.py::TestRunHistoryCommand::test_run_history_human_output
      -- 03-05 Task 2's own acceptance criterion.

    Same plan-internal criterion inconsistency as 03-01/03-04; this alias makes
    both executable commands green without duplicating test logic.
    """
    TestRunHistoryCommand().test_run_history_human_output(capsys, main_engine, monkeypatch)


class TestJobsCommand:
    def test_jobs_list_empty(self, capsys, main_session):
        assert main(["jobs", "list"]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "no jobs" in out
        
    def test_jobs_list_with_low_scorer(self, capsys, main_session):
        import uuid
        from datetime import datetime, timezone
        from huntloop.db.models import Company, Job
        
        c = Company(id=uuid.uuid4(), name="LowScoreComp", enabled=True, ats_identifier="low")
        j = Job(id=uuid.uuid4(), company_id=c.id, title="Test Job", url="http", dedup_key="k1", first_seen_at=datetime.now(timezone.utc), score_overall=1.0)
        main_session.add(c)
        main_session.add(j)
        main_session.commit()
        
        assert main(["jobs", "list"]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "LowScoreComp" in out
        assert "1.00" in out
        
    def test_jobs_list_min_score(self, capsys, main_session):
        import uuid
        from datetime import datetime, timezone
        from huntloop.db.models import Company, Job
        
        c = Company(id=uuid.uuid4(), name="MinScoreComp", enabled=True, ats_identifier="min")
        j1 = Job(id=uuid.uuid4(), company_id=c.id, title="Low Job", url="http", dedup_key="k1", first_seen_at=datetime.now(timezone.utc), score_overall=2.0)
        j2 = Job(id=uuid.uuid4(), company_id=c.id, title="High Job", url="http2", dedup_key="k2", first_seen_at=datetime.now(timezone.utc), score_overall=4.5)
        main_session.add_all([c, j1, j2])
        main_session.commit()
        
        assert main(["jobs", "list", "--min-score", "4.0", "--company", "MinScoreComp"]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "High Job" in out
        assert "Low Job" not in out
        
    def test_jobs_list_json(self, capsys, main_session):
        import uuid
        from datetime import datetime, timezone
        from huntloop.db.models import Company, Job
        
        c = Company(id=uuid.uuid4(), name="JsonComp", enabled=True, ats_identifier="json")
        j = Job(id=uuid.uuid4(), company_id=c.id, title="Test Job", url="http", dedup_key="k1", first_seen_at=datetime.now(timezone.utc), score_overall=5.0)
        main_session.add(c)
        main_session.add(j)
        main_session.commit()
        
        assert main(["jobs", "list", "--json", "--company", "JsonComp"]) == EXIT_OK
        out, _ = capsys.readouterr()
        data = json.loads(out)
        assert len(data) >= 1
        assert data[0]["company"] == "JsonComp"

class TestSchedulerCommand:
    def test_scheduler_start_builds_and_starts(self, capsys, monkeypatch):
        from huntloop.config import load_config

        started = []

        class FakeScheduler:
            def get_job(self, job_id):
                return None

            def start(self):
                started.append(True)

        monkeypatch.setattr(
            "huntloop.cli.scheduler.build_scheduler", lambda cfg: FakeScheduler()
        )
        # 04-06: boot resolves the settings overlay; pin it so no DB schema is
        # required to exercise the start path.
        monkeypatch.setattr(
            "huntloop.cli.scheduler.load_effective_config", lambda session: load_config()
        )
        assert main(["scheduler", "start"]) == EXIT_OK
        assert started == [True], "scheduler.start() must be called exactly once"

    def test_scheduler_start_config_error_aborts(self, capsys, monkeypatch):
        def _raise(session):
            raise ConfigError("bad tz")

        monkeypatch.setattr("huntloop.cli.scheduler.load_effective_config", _raise)
        assert main(["scheduler", "start"]) == EXIT_ABORTED
        out, err = capsys.readouterr()
        assert "bad tz" in err

    def test_scheduler_subcommand_is_registered(self, capsys):
        assert main([]) == EXIT_OK
        out, _ = capsys.readouterr()
        assert "scheduler" in out
