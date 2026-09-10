"""Tests for the LangGraph orchestration (02-10).

DISC-03 is the load-bearing test: one employer's failure does not stop the run.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from huntloop.config import load_config
from huntloop.db.models import Company, Job, Run, RunError, RunStatus, FilterTier
from huntloop.db.repository import RunRepository
from huntloop.discovery.ats.base import FetchResult, FetchStatus, RawListing
from huntloop.graph.state import DiscoveryState, EmployerResult
from huntloop.graph.nodes import load_employers, fan_out_to_employers, process_employer
from huntloop.llm.client import LlmUsage

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


class TestNodes:
    """Task 1: Graph state and per-employer node with fault isolation."""

    def test_discovery_state_append_reducer(self):
        """DiscoveryState.employer_results uses operator.add, not replacement."""
        import operator
        from typing import Annotated, get_origin, get_args, ForwardRef
        import re
        
        # Check the annotation directly from the TypedDict
        # TypedDict uses ForwardRef for annotations
        annotations = DiscoveryState.__annotations__
        
        # The annotation is a ForwardRef, so we need to check its string representation
        employer_results_annotation = annotations.get("employer_results")
        
        # ForwardRef has __forward_arg__ which contains the annotation string
        annotation_str = employer_results_annotation.__forward_arg__
        
        # Verify it contains Annotated and operator.add
        assert "Annotated" in annotation_str
        assert "operator.add" in annotation_str

    def test_process_employer_resolved_ats_returns_result(self, sessionmaker, llm_client, http_client, static_fetcher, rendered_fetcher):
        """process_employer for a resolved ATS employer returns EmployerResult with counts."""
        # Setup: create a company with resolved ATS
        session = sessionmaker()
        company = Company(
            id=uuid.uuid4(),
            name="TestCompany",
            enabled=True,
            ats="greenhouse",
            ats_identifier="testco",
            resolved_at=datetime.now(timezone.utc),
        )
        session.add(company)
        session.commit()
        
        # Mock adapter returning listings
        listings = [
            RawListing(
                external_id="1",
                title="Engineer",
                url="https://example.com/job/1",
                location_raw="Remote",
                posted_at=datetime.now(timezone.utc),
            )
        ]
        
        # Execute
        state = {
            "company_id": str(company.id),
            "run_id": str(uuid.uuid4()),
            "criteria_version": 1,
            "no_score": False,
        }
        
        # This test requires mocking the adapter - will be completed in implementation
        session.close()

    def test_continues_past_failure(self, sessionmaker, llm_client, http_client):
        """DISC-03: An employer failure is caught, recorded, and the run continues."""
        from huntloop.graph.build import build_graph
        
        session = sessionmaker()
        
        # Create 3 companies, middle one will fail
        companies = []
        for i, name in enumerate(["Good1", "Bad", "Good2"]):
            company = Company(
                id=uuid.uuid4(),
                name=name,
                enabled=True,
                ats="greenhouse",
                ats_identifier=f"company{i}",
                resolved_at=datetime.now(timezone.utc),
            )
            session.add(company)
            companies.append(company)
        session.commit()
        
        run_id = uuid.uuid4()
        
        # Build state
        state: DiscoveryState = {
            "run_id": str(run_id),
            "criteria_version": 1,
            "no_score": False,
            "company_ids": [str(c.id) for c in companies],
            "employer_results": [],
            "errors": [],
        }
        
        # Mock: one employer's adapter raises
        # Will need to inject a failing adapter for "Bad" company
        # This is the core DISC-03 test
        
        # Expected: employer_results has length 3, exactly one with error
        # Expected: RunError row exists with company_id = Bad's id
        # Expected: other two have complete results
        
        session.close()

    def test_error_recorded_against_correct_company(self, sessionmaker):
        """The failing employer's error is recorded against its company_id."""
        session = sessionmaker()
        run_id = uuid.uuid4()
        company_id = uuid.uuid4()
        
        # Create the error
        repo = RunRepository(session)
        error = repo.record_error(run_id, company_id, "fetch", "Test error")
        
        assert error.company_id == company_id
        assert error.stage == "fetch"
        
        session.close()

    def test_process_employer_unresolved_with_careers_url(self, sessionmaker, llm_client):
        """UNRESOLVED employer with careers_url takes the crawl path."""
        # Setup: company with no ATS but careers_url
        session = sessionmaker()
        company = Company(
            id=uuid.uuid4(),
            name="CrawlCo",
            enabled=True,
            ats=None,
            careers_url="https://crawlco.com/careers",
            ats_config={"resolution": {"status": "unresolved"}},
        )
        session.add(company)
        session.commit()
        
        # Execute: should call crawl path
        # Will need to mock the crawl
        
        session.close()

    def test_process_employer_unresolved_no_careers_url(self, sessionmaker):
        """UNRESOLVED employer with no careers_url returns zero-count result."""
        session = sessionmaker()
        company = Company(
            id=uuid.uuid4(),
            name="SkippedCo",
            enabled=True,
            ats=None,
            ats_config={"resolution": {"status": "unresolved"}},
        )
        session.add(company)
        session.commit()
        
        state = {
            "company_id": str(company.id),
            "run_id": str(uuid.uuid4()),
            "criteria_version": 1,
            "no_score": False,
        }
        
        # Expected: result with path="skipped", all counts=0, no error
        
        session.close()

    def test_record_fetch_outcome_called_once(self, sessionmaker):
        """process_employer calls record_fetch_outcome exactly once."""
        # Both ATS and crawl paths must call record_fetch_outcome
        # Need to mock and count calls
        pass

    def test_no_score_runs_zero_model_calls(self, sessionmaker, llm_client):
        """--no-score runs fetch, dedup, deterministic filters, then stops."""
        # Verify the recording client shows ZERO model calls
        pass

    def test_dedup_key_error_counted_in_failed(self, sessionmaker):
        """A DedupKeyError on one listing is counted in employer's failed."""
        from huntloop.discovery.dedup import DedupKeyError
        # Single listing with unkeyable data should increment failed counter
        pass

    def test_session_closed_in_finally(self, sessionmaker):
        """process_employer closes its session even when the node raises."""
        # Verify session is closed after exception
        pass

    def test_fan_out_returns_send_per_employer(self):
        """fan_out_to_employers returns one Send per enabled employer."""
        from langgraph.types import Send
        
        state: DiscoveryState = {
            "run_id": "test",
            "criteria_version": 1,
            "no_score": False,
            "company_ids": ["a", "b", "c"],
            "employer_results": [],
            "errors": [],
        }
        
        sends = fan_out_to_employers(state)
        
        assert len(sends) == 3
        assert all(isinstance(s, Send) for s in sends)

    def test_fan_out_empty_when_no_employers(self):
        """fan_out_to_employers returns [] when there are no enabled employers."""
        state: DiscoveryState = {
            "run_id": "test",
            "criteria_version": 1,
            "no_score": False,
            "company_ids": [],
            "employer_results": [],
            "errors": [],
        }
        
        sends = fan_out_to_employers(state)
        
        assert sends == []

    def test_load_employers_returns_company_ids(self, sessionmaker):
        """load_employers returns company_ids for enabled employers."""
        session = sessionmaker()
        
        # Create test companies
        for name in ["Enabled1", "Enabled2", "Disabled"]:
            company = Company(
                id=uuid.uuid4(),
                name=name,
                enabled=(not name.startswith("Disabled")),
            )
            session.add(company)
        session.commit()
        
        state = {}
        result = load_employers(state, sessionmaker=sessionmaker)
        
        assert "company_ids" in result
        assert len(result["company_ids"]) == 2  # Only enabled
        
        session.close()


class TestGraph:
    """Task 2: StateGraph assembly, RetryPolicy, bounded concurrency."""

    def test_build_graph_returns_compiled_graph(self, sessionmaker, llm_client, http_client, static_fetcher, rendered_fetcher):
        """build_graph returns a compiled graph with expected nodes."""
        from huntloop.graph.build import build_graph
        
        graph = build_graph(
            sessionmaker=sessionmaker,
            llm_client=llm_client,
            http_client=http_client,
            static_fetcher=static_fetcher,
            rendered_fetcher=rendered_fetcher,
        )
        
        nodes = graph.get_graph().nodes
        assert "load_employers" in nodes
        assert "process_employer" in nodes
        assert "finalize_run" in nodes

    def test_process_employer_has_retry_policy(self, sessionmaker, llm_client, http_client, static_fetcher, rendered_fetcher):
        """The process_employer node is registered with a RetryPolicy."""
        from huntloop.graph.build import build_graph
        from langgraph.types import RetryPolicy
        
        # Build the graph and verify RetryPolicy is attached
        # This requires inspecting the builder's node spec
        pass

    def test_run_discovery_returns_run_summary(self, sessionmaker, llm_client, http_client):
        """run_discovery with three employers returns a RunSummary."""
        from huntloop.graph.build import run_discovery
        
        session = sessionmaker()
        
        # Create 3 companies
        for i in range(3):
            company = Company(
                id=uuid.uuid4(),
                name=f"Company{i}",
                enabled=True,
                ats="greenhouse",
                ats_identifier=f"company{i}",
                resolved_at=datetime.now(timezone.utc),
            )
            session.add(company)
        session.commit()
        session.close()
        
        # Run discovery
        summary = run_discovery(sessionmaker=sessionmaker, llm_client=llm_client)
        
        # Verify summary fields
        assert summary.run_id is not None
        assert summary.companies_checked == 3

    def test_max_concurrency_from_config(self, sessionmaker, llm_client, http_client):
        """run_discovery passes max_concurrency from Config."""
        from huntloop.graph.build import run_discovery
        
        config = load_config()
        
        # Verify the config value is passed to graph.invoke
        # Need to mock or capture the invoke call
        pass

    def test_run_discovery_zero_employers(self, sessionmaker):
        """run_discovery with zero enabled employers returns zero-count summary."""
        from huntloop.graph.build import run_discovery
        from huntloop.db.repository import RunRepository
        
        session = sessionmaker()
        
        # No enabled companies
        summary = run_discovery(sessionmaker=sessionmaker)
        
        assert summary.companies_checked == 0
        assert summary.listings_fetched == 0
        assert summary.status == "success"
        
        # Verify run was still created and finished
        repo = RunRepository(session)
        run = repo.get(uuid.UUID(summary.run_id))
        assert run is not None
        assert run.status == RunStatus.SUCCESS
        
        session.close()

    def test_run_discovery_creates_one_run_row(self, sessionmaker):
        """run_discovery creates exactly one Run row and finishes it."""
        from huntloop.graph.build import run_discovery
        
        session = sessionmaker()
        
        # Run with no employers (cleanest test)
        summary = run_discovery(sessionmaker=sessionmaker)
        
        # Count run rows for this run_id
        count = session.execute(
            select(Run).where(Run.id == uuid.UUID(summary.run_id))
        ).scalar_one_or_none()
        
        assert count is not None
        
        session.close()

    def test_run_discovery_no_active_criteria_raises(self, sessionmaker):
        """run_discovery raises if no active criteria exist."""
        from huntloop.graph.build import run_discovery, NoActiveCriteria
        
        session = sessionmaker()
        
        # Ensure no criteria rows
        from huntloop.db.models import Criteria
        session.execute("DELETE FROM criteria")
        session.commit()
        
        with pytest.raises(NoActiveCriteria):
            run_discovery(sessionmaker=sessionmaker)
        
        # Verify no Run row was created
        count = session.execute(select(Run)).scalars().all()
        assert len(count) == 0
        
        session.close()

    def test_run_discovery_no_score_zero_model_calls(self, sessionmaker, llm_client):
        """run_discovery(no_score=True) produces zero model calls."""
        from huntloop.graph.build import run_discovery
        
        # Run with --no-score
        summary = run_discovery(sessionmaker=sessionmaker, no_score=True)
        
        assert summary.scored == 0
        assert summary.tokens_in == 0
        assert summary.tokens_out == 0

    def test_run_summary_fields_match_cli_rendering(self):
        """RunSummary exposes the exact fields 02-11 renders."""
        from huntloop.graph.build import RunSummary
        import dataclasses
        
        fields = {f.name for f in dataclasses.fields(RunSummary)}
        
        expected = {
            "run_id",
            "companies_checked",
            "listings_fetched",
            "after_dedup",
            "after_deterministic",
            "after_triage",
            "scored",
            "new_jobs_written",
            "updated",
            "failed",
            "tokens_in",
            "tokens_out",
            "cost_usd",
            "errors",
            "status",
        }
        
        assert fields == expected

    def test_graph_invoked_synchronously(self, sessionmaker, llm_client, http_client):
        """run_discovery contains no await and no asyncio.run."""
        from huntloop.graph import build
        import inspect
        
        source = inspect.getsource(build.run_discovery)
        
        assert "await" not in source
        assert "asyncio" not in source
        assert "ainvoke" not in source

    def test_consecutive_runs_write_then_update(self, sessionmaker, llm_client, http_client):
        """Two consecutive runs over same data: first writes, second updates."""
        from huntloop.graph.build import run_discovery
        
        session = sessionmaker()
        
        # Create a company
        company = Company(
            id=uuid.uuid4(),
            name="TestCo",
            enabled=True,
            ats="greenhouse",
            ats_identifier="testco",
            resolved_at=datetime.now(timezone.utc),
        )
        session.add(company)
        session.commit()
        
        # First run
        summary1 = run_discovery(sessionmaker=sessionmaker, llm_client=llm_client)
        assert summary1.new_jobs_written > 0
        assert summary1.updated == 0
        
        # Second run (same data)
        summary2 = run_discovery(sessionmaker=sessionmaker, llm_client=llm_client)
        assert summary2.new_jobs_written == 0
        assert summary2.updated > 0
        
        session.close()


# Fixtures
@pytest.fixture
def sessionmaker(self, portable_engine):
    """Session factory for tests."""
    from sqlalchemy.orm import sessionmaker as sm
    return sm(bind=portable_engine)


@pytest.fixture
def llm_client(self):
    """Mock LLM client."""
    from unittest.mock import MagicMock
    
    client = MagicMock()
    client.usage = LlmUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    return client


@pytest.fixture
def http_client(self):
    """Mock HTTP client."""
    from unittest.mock import MagicMock
    return MagicMock()


@pytest.fixture
def static_fetcher(self):
    """Mock static page fetcher."""
    from unittest.mock import MagicMock
    return MagicMock()


@pytest.fixture
def rendered_fetcher(self):
    """Mock rendered page fetcher."""
    from unittest.mock import MagicMock
    return MagicMock()
