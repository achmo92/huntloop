"""Tests for DISC-05 fallback crawler and LLM extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from huntloop.discovery.ats.base import FetchStatus
from huntloop.discovery.fetch.page import PageResult
from huntloop.discovery.crawl.careers import (
    CrawlResult,
    CrawledPage,
    content_hash,
    crawl_careers,
    same_origin,
)
from huntloop.discovery.crawl.extract import extract_listings, to_fetch_result


class RecordingFetcher:
    def __init__(self, responses: dict[str, PageResult | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def fetch(self, url: str):
        self.calls.append(url)
        resp = self.responses.get(url, PageResult(ok=False, url=url, final_url=url, status_code=404, html="", rendered=False, truncated=False, error="not found"))
        if isinstance(resp, Exception):
            raise resp
        return resp


class RecordingClient:
    """A fake OpenAI client that returns a sequence of responses and records arguments."""
    
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


class TestCrawl:
    def test_same_origin(self):
        # Registrable domain match
        assert same_origin('https://careers.acme.com/x', 'https://acme.com')
        assert same_origin('https://acme.com/jobs', 'https://acme.com')
        
        # Attack suffix
        assert not same_origin('https://acme.com.attacker.net/x', 'https://acme.com')
        
        # Completely different
        assert not same_origin('https://evil.example/x', 'https://acme.com')

    def test_content_hash_ignores_scripts(self):
        a = '<p>Engineer</p><script>var n=1</script>'
        b = '<p>Engineer</p><script>var n=999</script>'
        assert content_hash(a) == content_hash(b)
        assert content_hash(a) != content_hash('<p>Designer</p>')

    def test_crawl_short_circuits_on_unchanged_hash(self):
        html = "<html><body><p>Jobs</p></body></html>"
        fetcher = RecordingFetcher({
            "https://acme.com/careers": PageResult(ok=True, url="https://acme.com/careers", final_url="https://acme.com/careers", status_code=200, html=html, rendered=False, truncated=False, error=None)
        })
        
        h = content_hash(html)
        result = crawl_careers(fetcher, "https://acme.com/careers", previous_hash=h)
        
        assert result.skipped is True
        assert result.reason == "unchanged"
        assert len(result.pages) == 0
        assert len(fetcher.calls) == 1

    def test_crawl_follows_same_origin_bounds(self):
        html = '''
        <a href="https://acme.com/jobs/1">Job 1</a>
        <a href="https://evil.example/jobs">Evil</a>
        <a href="/jobs/2">Job 2</a>
        '''
        fetcher = RecordingFetcher({
            "https://acme.com/careers": PageResult(ok=True, url="https://acme.com/careers", final_url="https://acme.com/careers", status_code=200, html=html, rendered=False, truncated=False, error=None),
            "https://acme.com/jobs/1": PageResult(ok=True, url="https://acme.com/jobs/1", final_url=None, status_code=200, html="", rendered=False, truncated=False, error=None),
            "https://acme.com/jobs/2": PageResult(ok=True, url="https://acme.com/jobs/2", final_url=None, status_code=200, html="", rendered=False, truncated=False, error=None),
        })
        
        result = crawl_careers(fetcher, "https://acme.com/careers")
        
        assert result.skipped is False
        assert len(result.pages) == 3
        
        # Ensure evil was never called
        assert "https://evil.example/jobs" not in fetcher.calls

    def test_crawl_respects_max_pages(self):
        html = "".join([f'<a href="/jobs/{i}">Job {i}</a>' for i in range(10)])
        fetcher = RecordingFetcher({
            "https://acme.com/careers": PageResult(ok=True, url="https://acme.com/careers", final_url="https://acme.com/careers", status_code=200, html=html, rendered=False, truncated=False, error=None),
        })
        # Add the other pages so they don't 404
        for i in range(10):
            fetcher.responses[f"https://acme.com/jobs/{i}"] = PageResult(ok=True, url=f"https://acme.com/jobs/{i}", final_url=None, status_code=200, html="", rendered=False, truncated=False, error=None)

        result = crawl_careers(fetcher, "https://acme.com/careers", max_pages=5)
        
        # 1 base page + 4 detail pages = 5 pages total
        assert len(result.pages) == 5
        assert len(fetcher.calls) == 5

    def test_link_prioritisation(self):
        html = '''
        <a href="/about">About</a>
        <a href="/jobs">Jobs</a>
        <a href="/contact">Contact</a>
        '''
        fetcher = RecordingFetcher({
            "https://acme.com/": PageResult(ok=True, url="https://acme.com/", final_url="https://acme.com/", status_code=200, html=html, rendered=False, truncated=False, error=None),
            "https://acme.com/about": PageResult(ok=True, url="https://acme.com/about", final_url=None, status_code=200, html="", rendered=False, truncated=False, error=None),
            "https://acme.com/jobs": PageResult(ok=True, url="https://acme.com/jobs", final_url=None, status_code=200, html="", rendered=False, truncated=False, error=None),
            "https://acme.com/contact": PageResult(ok=True, url="https://acme.com/contact", final_url=None, status_code=200, html="", rendered=False, truncated=False, error=None),
        })
        
        crawl_careers(fetcher, "https://acme.com/", max_pages=2)
        
        # Max pages 2 means base page + 1 link. The priority link is /jobs.
        assert "https://acme.com/jobs" in fetcher.calls
        assert "https://acme.com/about" not in fetcher.calls

    def test_base_404_returns_gracefully(self):
        fetcher = RecordingFetcher({})
        result = crawl_careers(fetcher, "https://acme.com/")
        
        assert len(result.pages) == 0
        assert "fetch failed: 404" in result.reason

    def test_full_shell_without_job_links_triggers_rendering(self):
        """Found live at the 02-12 checkpoint (Atlassian): a static shell with
        plenty of nav text but zero job-detail links must trigger the rendered
        fetch — the 400-char threshold alone misses it. And once rendered, the
        job-detail links must be crawled before locale variants."""
        # >400 chars of nav text, but only locale-variant links (no job segments)
        nav_text = "<div>" + ("Navigation Menu Item " * 40) + "</div>"
        static_html = f'''<html><body>{nav_text}
        <a href="/ja/company/careers">JA</a>
        <a href="/fr/company/careers">FR</a>
        </body></html>'''
        rendered_html = '''<html><body><div>Principal Data Scientist Bengaluru or Remote</div>
        <a href="/ja/company/careers">JA</a>
        <a href="/fr/company/careers">FR</a>
        <a href="/company/careers/details/27069">Job A</a>
        <a href="/company/careers/details/26576">Job B</a>
        <a href="/company/careers/details/26571">Job C</a>
        </body></html>'''

        def page(url, html, rendered):
            return PageResult(ok=True, url=url, final_url=url, status_code=200, html=html, rendered=rendered, truncated=False, error=None)

        static = RecordingFetcher({
            "https://acme.com/company/careers/all-jobs": page("https://acme.com/company/careers/all-jobs", static_html, False),
        })
        rendered = RecordingFetcher({
            "https://acme.com/company/careers/all-jobs": page("https://acme.com/company/careers/all-jobs", rendered_html, True),
            "https://acme.com/company/careers/details/27069": page("https://acme.com/company/careers/details/27069", "<p>Job A detail</p>", True),
            "https://acme.com/company/careers/details/26576": page("https://acme.com/company/careers/details/26576", "<p>Job B detail</p>", True),
            "https://acme.com/company/careers/details/26571": page("https://acme.com/company/careers/details/26571", "<p>Job C detail</p>", True),
        })

        result = crawl_careers(
            static, "https://acme.com/company/careers/all-jobs",
            max_pages=4, rendered_fetcher=rendered,
        )

        assert result.rendered is True
        assert len(result.pages) == 4  # base + 3 detail pages
        # Detail pages crawled, locale variants never touched
        assert "https://acme.com/company/careers/details/27069" in rendered.calls
        assert "https://acme.com/company/careers/details/26576" in rendered.calls
        assert "https://acme.com/ja/company/careers" not in rendered.calls
        assert "https://acme.com/fr/company/careers" not in rendered.calls
        # The rendered base text (with the job grid) is what extraction sees
        assert "Principal Data Scientist" in result.pages[0].text

    def test_static_page_with_job_links_skips_rendering(self):
        """A static page with plenty of text that already exposes job links
        must NOT pay for Chromium."""
        body_text = "<p>" + ("We are hiring great people everywhere. " * 20) + "</p>"
        static_html = f'{body_text}<a href="/jobs/1">Job 1</a><a href="/jobs/2">Job 2</a>'
        static = RecordingFetcher({
            "https://acme.com/careers": PageResult(ok=True, url="https://acme.com/careers", final_url="https://acme.com/careers", status_code=200, html=static_html, rendered=False, truncated=False, error=None),
        })
        rendered = RecordingFetcher({})

        result = crawl_careers(static, "https://acme.com/careers", rendered_fetcher=rendered)

        assert result.rendered is False
        assert rendered.calls == []

    def test_trailing_slash_variant_not_crawled_twice(self):
        """The base page and its slashless variant are the same page — the
        budget must not be spent on it twice."""
        html = '<a href="/careers">Careers</a><a href="/jobs/1">Job 1</a>'
        fetcher = RecordingFetcher({
            "https://acme.com/careers/": PageResult(ok=True, url="https://acme.com/careers/", final_url="https://acme.com/careers/", status_code=200, html=html, rendered=False, truncated=False, error=None),
            "https://acme.com/careers": PageResult(ok=True, url="https://acme.com/careers", final_url=None, status_code=200, html=html, rendered=False, truncated=False, error=None),
            "https://acme.com/jobs/1": PageResult(ok=True, url="https://acme.com/jobs/1", final_url=None, status_code=200, html="", rendered=False, truncated=False, error=None),
        })

        result = crawl_careers(fetcher, "https://acme.com/careers/", max_pages=8)

        assert "https://acme.com/careers" not in fetcher.calls
        assert len(result.pages) == 2  # base + /jobs/1

    def test_ats_config_roundtrip_with_sqlalchemy_hack(self, main_session):
        from huntloop.db.models import Company
        from huntloop.discovery.crawl.careers import load_crawl_hash, save_crawl_hash
        
        comp = Company(name="HashCorp", ats_config={"resolution": {"status": "resolved"}})
        main_session.add(comp)
        main_session.commit()
        
        save_crawl_hash(main_session, comp, "hash123")
        main_session.expire_all()  # ensure it's re-fetched
        
        refetched = main_session.query(Company).filter_by(name="HashCorp").one()
        # Ensure resolution block was preserved!
        assert refetched.ats_config["resolution"]["status"] == "resolved"
        
        loaded = load_crawl_hash(refetched)
        assert loaded == "hash123"

    def test_load_crawl_hash_graceful_on_none(self):
        from huntloop.discovery.crawl.careers import load_crawl_hash
        class FakeCompany:
            ats_config = None
        assert load_crawl_hash(FakeCompany()) is None


class TestExtraction:
    def test_extract_listings_success(self):
        client = RecordingClient([
            {
                "listings": [
                    {
                        "title": "Backend Dev",
                        "url": "/roles/backend",
                        "location": "Remote",
                        "description": "Python work",
                        "posted": "2024-01-01",
                        "compensation": "$150k"
                    }
                ]
            }
        ])
        
        crawl_result = CrawlResult(
            base_url="https://acme.com/",
            pages=(CrawledPage(url="https://acme.com/careers", text="Backend Dev /roles/backend", hash="h"),)
        )
        
        listings = extract_listings(client, crawl_result)
        
        assert len(listings) == 1
        lst = listings[0]
        assert lst.title == "Backend Dev"
        assert lst.url == "https://acme.com/roles/backend"  # resolved
        assert lst.location_raw == "Remote"
        assert lst.description_plain == "Python work"
        assert lst.comp_raw == "$150k"
        assert lst.external_id is None
        assert lst.raw["source"] == "crawl"

    def test_dropped_fabricated_url(self):
        client = RecordingClient([
            {
                "listings": [
                    {
                        "title": "Fabricated Dev",
                        "url": "https://invented.com/job",  # Nowhere in page text
                        "location": "Remote",
                    }
                ]
            }
        ])
        
        crawl_result = CrawlResult(
            base_url="https://acme.com/",
            pages=(CrawledPage(url="https://acme.com/careers", text="Just text", hash="h"),)
        )
        
        listings = extract_listings(client, crawl_result)
        
        # Listing dropped because URL is fabricated
        assert len(listings) == 0

    def test_missing_fields_return_null(self):
        client = RecordingClient([
            {
                "listings": [
                    {
                        "title": "Minimal Dev",
                        "url": None,
                        "location": None,
                        "description": None,
                        "posted": None,
                        "compensation": None
                    }
                ]
            }
        ])
        
        crawl_result = CrawlResult(
            base_url="https://acme.com/",
            pages=(CrawledPage(url="https://acme.com/careers", text="Minimal Dev", hash="h"),)
        )
        
        listings = extract_listings(client, crawl_result)
        
        assert len(listings) == 1
        lst = listings[0]
        # url defaults to page.url when null
        assert lst.url == "https://acme.com/careers"
        assert lst.comp_raw is None
        assert lst.location_raw is None

    def test_empty_listings_returns_empty(self):
        client = RecordingClient([
            {
                "listings": []
            }
        ])
        
        crawl_result = CrawlResult(
            base_url="https://acme.com/",
            pages=(CrawledPage(url="https://acme.com/careers", text="No jobs here", hash="h"),)
        )
        
        listings = extract_listings(client, crawl_result)
        assert len(listings) == 0
        
        res = to_fetch_result(listings, crawl_result)
        assert res.status == FetchStatus.EMPTY

    def test_extraction_model_used(self):
        client = RecordingClient([{"listings": []}])
        crawl_result = CrawlResult(
            base_url="https://acme.com/",
            pages=(CrawledPage(url="https://acme.com/careers", text="Jobs", hash="h"),)
        )
        extract_listings(client, crawl_result)
        
        from huntloop.config import load_config
        assert client.calls[0]["model"] == load_config().extraction_model

    def test_no_div_in_user_message(self):
        client = RecordingClient([{"listings": []}])
        crawl_result = CrawlResult(
            base_url="https://acme.com/",
            # Raw HTML shouldn't make it to extraction anyway, but if it did, it should fail our `<div` test 
            # if we didn't sanitize. Here we just assert the user message uses text.
            pages=(CrawledPage(url="https://acme.com/careers", text="Just clean text", hash="h"),)
        )
        extract_listings(client, crawl_result)
        
        user_msg = next(m["content"] for m in client.calls[0]["messages"] if m["role"] == "user")
        assert "<div" not in user_msg

    def test_llm_error_surfaced_gracefully(self):
        from huntloop.llm.client import LlmResponseError
        client = RecordingClient([
            LlmResponseError("Bad JSON")
        ])
        
        crawl_result = CrawlResult(
            base_url="https://acme.com/",
            pages=(CrawledPage(url="https://acme.com/careers", text="Jobs", hash="h"),)
        )
        
        listings = extract_listings(client, crawl_result)
        assert len(listings) == 0
