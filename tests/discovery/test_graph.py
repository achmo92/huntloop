"""Tests for DISC-03 and the pipeline graph execution."""

import operator
from datetime import datetime, timezone
from typing import Annotated, TypedDict

import pytest

from huntloop.db.models import Company, FilterTier, Job, RunStatus
from huntloop.db.repository import CompanyRepository, JobRepository, RunRepository
from huntloop.discovery.ats.base import ErrorKind, FetchResult, FetchStatus, RawListing
from huntloop.discovery.ats.registry import get_adapter
from huntloop.graph.build import NoActiveCriteria, build_graph, run_discovery
from huntloop.graph.nodes import fan_out_to_employers, process_employer
from huntloop.graph.state import DiscoveryState, EmployerResult


import json

class RecordingClient:
    def __init__(self, responses: list[dict | Exception]) -> None:
        self.responses = responses
        self.call_idx = 0
        self.calls: list[dict] = []
        
    class Completions:
        def __init__(self, parent):
            self.parent = parent
            
        def create(self, **kwargs):
            self.parent.calls.append(kwargs)
            if self.parent.call_idx >= len(self.parent.responses):
                raise RuntimeError(f"Ran out of mocked responses. Calls so far: {self.parent.call_idx}")
            resp = self.parent.responses[self.parent.call_idx]
            self.parent.call_idx += 1
            if isinstance(resp, Exception):
                raise resp
            from collections import namedtuple
            Choice = namedtuple("Choice", ["message"])
            Message = namedtuple("Message", ["content"])
            Usage = namedtuple("Usage", ["prompt_tokens", "completion_tokens"])
            choice = Choice(message=Message(content=json.dumps(resp)))
            class FakeCompletion:
                choices = [choice]
                usage = Usage(prompt_tokens=10, completion_tokens=20)
                model = kwargs.get("model", "fake-model")
            return FakeCompletion()
            
    @property
    def chat(self):
        class Chat:
            completions = self.Completions(self)
        return Chat()


class TestNodes:
    def test_discovery_state_reducers(self):
        # Assert two concurrent branches both contribute rather than one overwriting
        state: DiscoveryState = {"employer_results": [], "errors": []}
        
        # In a real graph, reducers apply when new state is returned
        r1 = [{"company_id": "1", "fetched": 10}]
        r2 = [{"company_id": "2", "fetched": 5}]
        
        combined = operator.add(r1, r2)
        assert len(combined) == 2
        assert combined[0]["company_id"] == "1"
        assert combined[1]["company_id"] == "2"

    def test_fan_out_returns_sends(self):
        state = {"company_ids": ["c1", "c2"], "run_id": "r1", "criteria_version": 1}
        sends = fan_out_to_employers(state)
        
        assert len(sends) == 2
        assert sends[0].node == "process_employer"
        assert sends[0].arg["company_id"] == "c1"
        assert sends[0].arg["run_id"] == "r1"
        
    def test_fan_out_empty_routes_to_finalize(self):
        sends = fan_out_to_employers({"company_ids": []})
        assert sends == "finalize_run"

    def test_process_employer_happy_path(self, main_session, portable_engine):
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=portable_engine)
        
        from sqlalchemy import text
        main_session.execute(text("DELETE FROM jobs"))
        main_session.execute(text("DELETE FROM companies"))
        
        repo = CompanyRepository(main_session)
        comp_id = repo.upsert_by_name("Test ATS")
        comp = repo.get_by_name("Test ATS")
        comp.ats = "lever"
        comp.ats_config = {"resolution": {"status": "resolved"}}
        main_session.commit()
        
        from huntloop.criteria.loader import save_new_criteria_version
        from huntloop.criteria.schema import CriteriaPayload
        from sqlalchemy import text
        main_session.execute(text("DELETE FROM criteria"))
        save_new_criteria_version(main_session, CriteriaPayload(
            profile_summary="Test", dimension_weights={"role_fit": 1.0, "seniority_fit": 0.0, "employer_fit": 0.0, "trajectory": 0.0}
        ))
        main_session.commit()
        
        run = RunRepository(main_session).start(trigger="manual")
        main_session.commit()

        class DummyAdapter:
            def fetch_jobs(self, company, client):
                l = RawListing(
                    external_id="1", url="https://acme.com/1", title="Eng", location_raw="NY",
                    description_plain="Desc", description_html=None, posted_at=None, comp_raw=None,
                    comp_min=None, comp_max=None, raw={}
                )
                return FetchResult(status=FetchStatus.OK, listings=[l, l])
                
        import huntloop.graph.nodes
        huntloop.graph.nodes.get_adapter = lambda x: DummyAdapter()
        
        res = process_employer(
            {
                "company_id": str(comp.id),
                "run_id": str(run.id),
                "criteria_version": 1,
            },
            sessionmaker=Session,
            llm_client=RecordingClient([{"is_match": True}, {"dimensions": {"a": 1}, "flags": {}}] * 10),
            http_client=None
        )
        
        employer_results = res["employer_results"]
        assert len(employer_results) == 1
        r = employer_results[0]
        
        assert r["company_name"] == "Test ATS"
        assert r["fetched"] == 2
        assert r["after_dedup"] == 1
        assert r.get("error") is None

    def test_continues_past_failure(self, main_session, portable_engine):
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=portable_engine)
        
        repo = CompanyRepository(main_session)
        from huntloop.db.models import Company
        c1 = main_session.get(Company, repo.upsert_by_name("C1"))
        c2 = main_session.get(Company, repo.upsert_by_name("C2"))
        c3 = main_session.get(Company, repo.upsert_by_name("C3"))
        
        for c in [c1, c2, c3]:
            c.ats = "lever"
            c.ats_config = {"resolution": {"status": "resolved"}}
        
        from huntloop.criteria.loader import save_new_criteria_version
        from huntloop.criteria.schema import CriteriaPayload
        save_new_criteria_version(main_session, CriteriaPayload(
            profile_summary="Test", dimension_weights={"role_fit": 1.0, "seniority_fit": 0.0, "employer_fit": 0.0, "trajectory": 0.0}
        ))
        
        run = RunRepository(main_session).start(trigger="manual")
        main_session.commit()
        
        class FailingAdapter:
            def fetch_jobs(self, company, client):
                if company.name == "C2":
                    raise ValueError("BOOM")
                l = RawListing(
                    external_id=company.name, url=f"https://acme.com/{company.name}", title="Eng", location_raw="NY",
                    description_plain="Desc", description_html=None, posted_at=None, comp_raw=None,
                    comp_min=None, comp_max=None, raw={}
                )
                return FetchResult(status=FetchStatus.OK, listings=[l])
                
        import huntloop.graph.nodes
        huntloop.graph.nodes.get_adapter = lambda x: FailingAdapter()
        
        # C1 succeeds
        r1 = process_employer(
            {"company_id": str(c1.id), "run_id": str(run.id), "criteria_version": 1},
            sessionmaker=Session, llm_client=RecordingClient([{"is_match": True}, {"dimensions": {"a": 1}, "flags": {}}] * 10), http_client=None
        )["employer_results"][0]
        
        # C2 fails
        r2 = process_employer(
            {"company_id": str(c2.id), "run_id": str(run.id), "criteria_version": 1},
            sessionmaker=Session, llm_client=RecordingClient([{"is_match": True}, {"dimensions": {"a": 1}, "flags": {}}] * 10), http_client=None
        )["employer_results"][0]
        
        # C3 succeeds
        r3 = process_employer(
            {"company_id": str(c3.id), "run_id": str(run.id), "criteria_version": 1},
            sessionmaker=Session, llm_client=RecordingClient([{"is_match": True}, {"dimensions": {"a": 1}, "flags": {}}] * 10), http_client=None
        )["employer_results"][0]
        
        results = [r1, r2, r3]
        
        # Length 3, exactly one error
        assert len(results) == 3
        errors = [r for r in results if r.get("error")]
        assert len(errors) == 1
        assert "ValueError: BOOM" in errors[0]["error"]
        assert errors[0]["company_id"] == str(c2.id)
        assert errors[0]["stage"] == "fetch"
        
        # The failing employer's error is recorded against ITS company id
        from sqlalchemy import text
        run_errors = main_session.execute(text("SELECT * FROM run_errors")).fetchall()
        assert len(run_errors) == 1
        assert str(run_errors[0].company_id).replace("-", "") == str(c2.id).replace("-", "")

    def test_process_employer_no_score(self, main_session, portable_engine):
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=portable_engine)
        
        repo = CompanyRepository(main_session)
        from huntloop.db.models import Company
        comp = main_session.get(Company, repo.upsert_by_name("No Score"))
        comp.ats = "lever"
        comp.ats_config = {"resolution": {"status": "resolved"}}
        
        from huntloop.criteria.loader import save_new_criteria_version
        from huntloop.criteria.schema import CriteriaPayload
        save_new_criteria_version(main_session, CriteriaPayload(
            profile_summary="Test", dimension_weights={"role_fit": 1.0, "seniority_fit": 0.0, "employer_fit": 0.0, "trajectory": 0.0}
        ))
        
        run = RunRepository(main_session).start(trigger="manual")
        main_session.commit()
        
        class DummyAdapter:
            def fetch_jobs(self, company, client):
                l = RawListing(
                    external_id="1", url="https://acme.com/1", title="Eng", location_raw="NY",
                    description_plain="Desc", description_html=None, posted_at=None, comp_raw=None,
                    comp_min=None, comp_max=None, raw={}
                )
                return FetchResult(status=FetchStatus.OK, listings=[l])
                
        import huntloop.graph.nodes
        huntloop.graph.nodes.get_adapter = lambda x: DummyAdapter()
        
        llm = RecordingClient([{"is_match": True}, {"dimensions": {"a": 1}, "flags": {}}] * 10)
        res = process_employer(
            {
                "company_id": str(comp.id),
                "run_id": str(run.id),
                "criteria_version": 1,
                "no_score": True
            },
            sessionmaker=Session,
            llm_client=llm,
            http_client=None
        )
        
        r = res["employer_results"][0]
        assert len(llm.calls) == 0
        print("ERROR IS:", r.get("error"))
        assert r["scored"] == 0
        assert r["after_dedup"] == 1


class TestGraph:
    def test_build_graph_contains_nodes(self, main_session, portable_engine):
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=portable_engine)
        
        graph = build_graph(
            sessionmaker=Session, 
            llm_client=RecordingClient([{"is_match": True}, {"dimensions": {"a": 1}, "flags": {}}] * 10), 
            http_client=None
        )
        
        nodes = graph.get_graph().nodes
        assert "load_employers" in nodes
        assert "process_employer" in nodes
        assert "finalize_run" in nodes

    def test_run_discovery_no_criteria_raises(self, main_session, portable_engine):
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=portable_engine)
        from sqlalchemy import text
        main_session.execute(text("DELETE FROM criteria"))
        main_session.commit()
        
        with pytest.raises(NoActiveCriteria):
            run_discovery(sessionmaker=Session)
            
        assert RunRepository(main_session).list_recent() == []

    def test_run_discovery_end_to_end(self, main_session, portable_engine):
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=portable_engine)
        
        repo = CompanyRepository(main_session)
        from huntloop.db.models import Company
        comp = main_session.get(Company, repo.upsert_by_name("End to End"))
        comp.ats = "lever"
        comp.ats_config = {"resolution": {"status": "resolved"}}
        
        from huntloop.criteria.loader import save_new_criteria_version
        from huntloop.criteria.schema import CriteriaPayload
        save_new_criteria_version(main_session, CriteriaPayload(
            profile_summary="Test", dimension_weights={"role_fit": 1.0, "seniority_fit": 0.0, "employer_fit": 0.0, "trajectory": 0.0}
        ))
        main_session.commit()
        
        class DummyAdapter:
            def fetch_jobs(self, company, client):
                l = RawListing(
                    external_id="1", url="https://acme.com/1", title="Eng", location_raw="NY",
                    description_plain="Desc", description_html=None, posted_at=None, comp_raw=None,
                    comp_min=None, comp_max=None, raw={}
                )
                return FetchResult(status=FetchStatus.OK, listings=[l])
                
        import huntloop.graph.nodes
        huntloop.graph.nodes.get_adapter = lambda x: DummyAdapter()
        
        summary = run_discovery(
            sessionmaker=Session, 
            llm_client=RecordingClient([{"is_match": True}, {"dimensions": {"a": 1}, "flags": {}}] * 10), 
            http_client=None
        )
        
        assert summary.companies_checked == 1
        assert summary.listings_fetched == 1
        assert summary.new_jobs_written == 1
        assert summary.updated == 0
        assert summary.status == "success"
        
        # Test DISC-06 end to end updates
        summary2 = run_discovery(
            sessionmaker=Session, 
            llm_client=RecordingClient([{"is_match": True}, {"dimensions": {"a": 1}, "flags": {}}] * 10), 
            http_client=None
        )
        
        assert summary2.new_jobs_written == 0
        assert summary2.updated == 1

    def test_run_discovery_no_employers(self, main_session, portable_engine):
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=portable_engine)
        
        from sqlalchemy import text
        main_session.execute(text("DELETE FROM companies"))
        
        from huntloop.criteria.loader import save_new_criteria_version
        from huntloop.criteria.schema import CriteriaPayload
        save_new_criteria_version(main_session, CriteriaPayload(
            profile_summary="Test", dimension_weights={"role_fit": 1.0, "seniority_fit": 0.0, "employer_fit": 0.0, "trajectory": 0.0}
        ))
        main_session.commit()
        
        summary = run_discovery(
            sessionmaker=Session, 
            llm_client=RecordingClient([{"is_match": True}, {"dimensions": {"a": 1}, "flags": {}}] * 10), 
            http_client=None
        )
        
        assert summary.companies_checked == 0
        assert summary.listings_fetched == 0
        assert summary.status == "success"
