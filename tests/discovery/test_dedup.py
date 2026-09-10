"""Tests for DISC-06 deterministic dedup key logic."""

from __future__ import annotations

import subprocess
import sys
from textwrap import dedent

import pytest

from huntloop.discovery.ats.base import RawListing
from huntloop.discovery.dedup import DedupKeyError, compute_dedup_key, normalize_url


class TestDedupKey:
    def test_compute_dedup_key_with_external_id(self):
        listing = RawListing(
            external_id="4567",
            url="https://acme.com/jobs/1",
            title="Engineer",
            location_raw="Remote",
            description_plain="Job",
            description_html=None,
            posted_at=None,
            comp_raw=None,
            comp_min=None,
            comp_max=None,
            raw={}
        )
        assert compute_dedup_key("Cobalt", listing) == "cobalt:4567"

    def test_company_slug_variants(self):
        listing = RawListing(
            external_id="123", url="https://x.com/1", title="Job", location_raw="Remote",
            description_plain="Job", description_html=None, posted_at=None, comp_raw=None,
            comp_min=None, comp_max=None, raw={}
        )
        assert compute_dedup_key("Cobalt.io", listing) == "cobaltio:123"
        assert compute_dedup_key("cobalt.io", listing) == "cobaltio:123"
        assert compute_dedup_key(" Cobalt.io ", listing) == "cobaltio:123"

    def test_different_companies_same_id(self):
        listing = RawListing(
            external_id="1", url="https://x.com/1", title="Job", location_raw="Remote",
            description_plain="Job", description_html=None, posted_at=None, comp_raw=None,
            comp_min=None, comp_max=None, raw={}
        )
        assert compute_dedup_key("A", listing) != compute_dedup_key("B", listing)

    def test_compute_dedup_key_with_url_only(self):
        listing = RawListing(
            external_id=None,
            url="https://acme.com/jobs/1",
            title="Engineer",
            location_raw="Remote",
            description_plain="Job",
            description_html=None,
            posted_at=None,
            comp_raw=None,
            comp_min=None,
            comp_max=None,
            raw={}
        )
        assert compute_dedup_key("Cobalt", listing) == "url:https://acme.com/jobs/1"

    def test_normalize_url_scheme_and_www(self):
        url1 = "http://www.acme.com/jobs/1"
        url2 = "https://acme.com/jobs/1"
        assert normalize_url(url1) == normalize_url(url2)
        assert normalize_url(url1) == "https://acme.com/jobs/1"

    def test_normalize_url_strips_tracking_keeps_identifying(self):
        url1 = "https://acme.com/jobs/1?utm_source=linkedin&gh_jid=123"
        url2 = "https://acme.com/jobs/1?gh_jid=123"
        assert normalize_url(url1) == normalize_url(url2)

    def test_normalize_url_sorts_params(self):
        url1 = "https://acme.com/jobs/1?b=2&a=1"
        url2 = "https://acme.com/jobs/1?a=1&b=2"
        assert normalize_url(url1) == normalize_url(url2)

    def test_normalize_url_strips_fragment(self):
        url1 = "https://acme.com/jobs/1#section"
        url2 = "https://acme.com/jobs/1"
        assert normalize_url(url1) == normalize_url(url2)

    def test_normalize_url_preserves_path_case(self):
        url = "https://acme.com/Jobs/ID/1"
        assert normalize_url(url) == "https://acme.com/Jobs/ID/1"

    def test_missing_identity_raises(self):
        listing = RawListing(
            external_id=None,
            url=None,
            title="Engineer",
            location_raw="Remote",
            description_plain="Job",
            description_html=None,
            posted_at=None,
            comp_raw=None,
            comp_min=None,
            comp_max=None,
            raw={}
        )
        with pytest.raises(DedupKeyError):
            compute_dedup_key("Cobalt", listing)

    def test_key_is_independent_of_title_and_time(self):
        listing1 = RawListing(
            external_id="1", url="https://x.com/1", title="Title 1", location_raw="Remote",
            description_plain="Job", description_html=None, posted_at=None, comp_raw=None,
            comp_min=None, comp_max=None, raw={}
        )
        listing2 = RawListing(
            external_id="1", url="https://x.com/1", title="Title 2", location_raw="Remote",
            description_plain="Job", description_html=None, posted_at=None, comp_raw=None,
            comp_min=None, comp_max=None, raw={}
        )
        # Identical key despite different title
        assert compute_dedup_key("A", listing1) == compute_dedup_key("A", listing2)
        assert "Title" not in compute_dedup_key("A", listing1)

    def test_cross_process_determinism(self):
        # Assert the key computation is stable across processes
        script = dedent("""
        from huntloop.discovery.ats.base import RawListing
        from huntloop.discovery.dedup import compute_dedup_key
        
        listing = RawListing(
            external_id="abc-123",
            url="https://acme.com/jobs/1?utm_source=twitter&gh_jid=999#header",
            title="Engineer",
            location_raw="Remote",
            description_plain="Job",
            description_html=None,
            posted_at=None,
            comp_raw=None,
            comp_min=None,
            comp_max=None,
            raw={}
        )
        print(compute_dedup_key("Cobalt.io", listing))
        """)
        
        out1 = subprocess.check_output([sys.executable, "-c", script], text=True).strip()
        out2 = subprocess.check_output([sys.executable, "-c", script], text=True).strip()
        
        assert out1 == out2
        assert out1 == "cobaltio:abc-123"

    def test_length_truncation_is_deterministic(self):
        long_url = "https://acme.com/jobs/" + "a" * 300
        listing = RawListing(
            external_id=None, url=long_url, title="Job", location_raw="Remote",
            description_plain="Job", description_html=None, posted_at=None, comp_raw=None,
            comp_min=None, comp_max=None, raw={}
        )
        
        key1 = compute_dedup_key("Cobalt", listing)
        key2 = compute_dedup_key("Cobalt", listing)
        
        assert key1 == key2
        assert len(key1) <= 240
        assert "#" in key1

class TestIdempotentWrite:
    def test_write_listing_idempotent(self, portable_engine):
        from sqlalchemy.orm import sessionmaker
        from huntloop.db.models import Company, FilterTier, RunTrigger
        from huntloop.db.repository import CompanyRepository, JobRepository, RunRepository
        from huntloop.discovery.write import write_listing
        from huntloop.scoring.pipeline import ScoredListing
        from datetime import datetime, timezone
        import uuid
        
        Session = sessionmaker(bind=portable_engine)
        session = Session()
        try:
            CompanyRepository(session).upsert_by_name("Idempotent Corp")
            comp = CompanyRepository(session).get_by_name("Idempotent Corp")
            run = RunRepository(session).start(trigger=RunTrigger.MANUAL)
            session.commit()
            
            listing = RawListing(
                external_id="idem-1", url="https://acme.com/jobs/1", title="Job",
                location_raw="Remote", description_plain="Job", description_html=None,
                posted_at=None, comp_raw=None, comp_min=None, comp_max=None, raw={}
            )
            
            scored = ScoredListing(
                listing=listing, scored=False, tier_reached=FilterTier.DETERMINISTIC,
                overall=None, dimensions=None, flags=None, summary="",
                criteria_version=None, rubric_version=None, model=None, note="", error="", usage=()
            )
            
            now1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
            res1 = write_listing(session, comp, scored, run_id=run.id, now=now1)
            session.commit()
            
            assert res1 == "inserted"
            assert JobRepository(session).count() == 1
            
            job1 = JobRepository(session).get_by_dedup_key("idempotentcorp:idem-1")
            assert job1.first_seen_at == now1
            assert job1.last_seen_at == now1
            
            now2 = datetime(2024, 1, 2, tzinfo=timezone.utc)
            res2 = write_listing(session, comp, scored, run_id=run.id, now=now2)
            session.commit()
            
            assert res2 == "updated"
            assert JobRepository(session).count() == 1
            
            job2 = JobRepository(session).get_by_dedup_key("idempotentcorp:idem-1")
            assert job2.id == job1.id
            assert job2.first_seen_at == now1
            assert job2.last_seen_at == now2
            
        finally:
            session.close()
