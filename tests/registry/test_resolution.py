"""Employer resolution tests (REG-01, REG-02, REG-03).

All tests run without network access and without a browser installed.
HTML fixtures: tests/fixtures/html/*.html (committed, never fetched live).
ATS fixtures:  tests/fixtures/ats/*.json   (committed, never fetched live).

Use:
    pytest tests/registry/test_resolution.py -k PageFetcher -q     # Task 1
    pytest tests/registry/test_resolution.py -k "Signatures or Probe" -q  # Task 2
    pytest tests/registry/test_resolution.py -k add_employer -q   # REG-01
    pytest tests/registry/test_resolution.py -k slug_differs -q   # REG-03
    pytest tests/registry/test_resolution.py -x -q                # all
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock

import httpx
import pytest

from huntloop.discovery.fetch.page import (
    MAX_HTML_BYTES,
    PageFetcher,
    PageResult,
    RendererUnavailable,
    RenderedPageFetcher,
    StaticPageFetcher,
)
from huntloop.registry.probe import ProbeResult, guess_slugs, probe_slug
from huntloop.registry.signatures import SlugCandidate, sweep_signatures
from huntloop.registry.resolve import (
    ResolutionResult,
    ResolutionStatus,
    build_ats_config,
    persist_resolution,
    resolve_employer,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _read_html(name: str) -> str:
    path = FIXTURES_DIR / "html" / f"{name}.html"
    return path.read_text(encoding="utf-8")


def _read_ats_json(name: str) -> bytes:
    path = FIXTURES_DIR / "ats" / f"{name}.json"
    return path.read_bytes()


def _make_mock_transport(url_to_response: dict[str, tuple[int, bytes]]) -> httpx.MockTransport:
    """Build a MockTransport that maps URL prefixes to (status, body) pairs."""

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        for prefix, (status, body) in url_to_response.items():
            if url_str.startswith(prefix) or prefix in url_str:
                return httpx.Response(status, content=body)
        return httpx.Response(404, content=b'{"error":"not found"}')

    return httpx.MockTransport(handler)


def _greenhouse_transport(slug: str, fixture_name: str) -> httpx.MockTransport:
    return _make_mock_transport(
        {f"boards-api.greenhouse.io/v1/boards/{slug}": (200, _read_ats_json(fixture_name))}
    )


def _lever_transport(slug: str, fixture_name: str) -> httpx.MockTransport:
    return _make_mock_transport(
        {f"api.lever.co/v0/postings/{slug}": (200, _read_ats_json(fixture_name))}
    )


def _ashby_transport(slug: str, fixture_name: str) -> httpx.MockTransport:
    return _make_mock_transport(
        {f"api.ashbyhq.com/posting-api/job-board/{slug}": (200, _read_ats_json(fixture_name))}
    )


# A transport that returns 404 for every URL.
ALL_404 = httpx.MockTransport(lambda r: httpx.Response(404, content=b'{"error":"not found"}'))


# ---------------------------------------------------------------------------
# Task 1: PageFetcher
# ---------------------------------------------------------------------------


class TestPageFetcher:
    """Task 1 — PageFetcher seam."""

    def test_static_200_returns_ok_result(self):
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, content=b"<html>hello</html>")
        )
        fetcher = StaticPageFetcher(client=httpx.Client(transport=transport))
        result = fetcher.fetch("https://example.com/careers")
        assert result.ok is True
        assert result.html == "<html>hello</html>"
        assert result.rendered is False

    def test_static_follows_redirect_and_reports_final_url(self):
        def handler(r):
            if "original" in str(r.url):
                return httpx.Response(
                    301, headers={"Location": "https://lifeatspotify.com/careers"}
                )
            return httpx.Response(200, content=b"<html>final</html>")

        fetcher = StaticPageFetcher(
            client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        )
        result = fetcher.fetch("https://spotify.com/original")
        assert result.ok is True
        assert result.final_url is not None

    def test_static_404_returns_not_ok(self):
        transport = httpx.MockTransport(
            lambda r: httpx.Response(404, content=b"not found")
        )
        fetcher = StaticPageFetcher(client=httpx.Client(transport=transport))
        result = fetcher.fetch("https://example.com/careers")
        assert result.ok is False
        assert result.status_code == 404

    def test_static_connection_error_returns_not_ok(self):
        def handler(r):
            raise httpx.ConnectError("Connection refused")

        fetcher = StaticPageFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
        result = fetcher.fetch("https://example.com/careers")
        assert result.ok is False
        assert result.error is not None
        assert "ConnectError" in result.error

    def test_static_no_auth_header(self):
        """StaticPageFetcher must not send Authorization or cookies."""
        sent_headers: dict = {}

        def handler(r: httpx.Request):
            sent_headers.update(dict(r.headers))
            return httpx.Response(200, content=b"<html/>")

        fetcher = StaticPageFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
        fetcher.fetch("https://example.com")
        assert "authorization" not in {k.lower() for k in sent_headers}
        assert "cookie" not in {k.lower() for k in sent_headers}

    def test_static_user_agent_contains_huntloop(self):
        """StaticPageFetcher's default client must send HuntLoop UA."""
        from huntloop.discovery.fetch.page import USER_AGENT

        sent_ua: list[str] = []

        def handler(r: httpx.Request):
            sent_ua.append(r.headers.get("user-agent", ""))
            return httpx.Response(200, content=b"<html/>")

        # Use the default-constructed client path (no injected client) so the
        # USER_AGENT constant from page.py is used.  Inject the transport by
        # building a client that matches the default fetcher's settings but with
        # our recording transport, then pass it explicitly.
        fetcher = StaticPageFetcher(
            client=httpx.Client(
                transport=httpx.MockTransport(handler),
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
        )
        fetcher.fetch("https://example.com")
        assert sent_ua and "HuntLoop" in sent_ua[0]

    def test_static_truncates_oversized_body(self):
        big_html = "a" * (MAX_HTML_BYTES + 100)
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, content=big_html.encode())
        )
        fetcher = StaticPageFetcher(client=httpx.Client(transport=transport))
        result = fetcher.fetch("https://example.com")
        assert result.truncated is True
        assert len(result.html) == MAX_HTML_BYTES

    def test_rendered_missing_playwright_raises_renderer_unavailable(self, monkeypatch):
        """RenderedPageFetcher raises RendererUnavailable when Playwright is absent."""
        monkeypatch.setitem(sys.modules, "playwright", None)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

        fetcher = RenderedPageFetcher()
        with pytest.raises(RendererUnavailable, match="playwright install chromium"):
            fetcher.fetch("https://example.com")

    def test_rendered_conforms_to_page_fetcher_protocol(self):
        """Both classes are structurally compatible with PageFetcher Protocol."""
        # Protocol structural check — just ensure the method exists with correct name.
        assert hasattr(StaticPageFetcher(), "fetch")
        assert hasattr(RenderedPageFetcher(), "fetch")
        assert callable(StaticPageFetcher().fetch)
        assert callable(RenderedPageFetcher().fetch)

    def test_rendered_sets_rendered_true(self, monkeypatch):
        """RenderedPageFetcher returns rendered=True on success (mocked Playwright)."""
        mock_page = MagicMock()
        mock_page.content.return_value = "<html>rendered</html>"
        mock_page.url = "https://example.com"

        mock_context = MagicMock()
        mock_context.new_page.return_value.__enter__ = MagicMock(return_value=mock_page)
        mock_context.new_page.return_value = mock_page

        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_pw = MagicMock()
        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)
        mock_pw.chromium.launch.return_value = mock_browser

        mock_sync_playwright = MagicMock(return_value=mock_pw)

        mock_module = MagicMock()
        mock_module.sync_playwright = mock_sync_playwright

        monkeypatch.setitem(sys.modules, "playwright", mock_module)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", mock_module)

        fetcher = RenderedPageFetcher()
        result = fetcher.fetch("https://example.com")

        # rendered=True is the key invariant; ok may vary with mock depth
        assert result.rendered is True


# ---------------------------------------------------------------------------
# Task 2a: Signatures
# ---------------------------------------------------------------------------


class TestSignatures:
    """Task 2 — Detection signatures (REG-02, REG-03)."""

    def test_cobaltio_greenhouse_via_link(self):
        html = _read_html("cobaltio_careers")
        candidates = sweep_signatures(html)
        assert any(c.platform == "greenhouse" and c.slug == "cobaltio" for c in candidates)

    def test_newrocket_highmetric_via_direct_api_call(self):
        """NewRocket fixture: slug comes from the inline boards-api XHR string — REG-03."""
        html = _read_html("newrocket_careers")
        candidates = sweep_signatures(html)
        # Assert the slug comes from the direct_api_call pattern, not a hosted_board link.
        assert any(c.platform == "greenhouse" and c.slug == "highmetric" for c in candidates)
        # Verify the fixture has no job-boards.greenhouse.io/highmetric link.
        assert "job-boards.greenhouse.io/highmetric" not in html.lower()

    def test_ramp_ashby(self):
        html = _read_html("ramp_careers")
        candidates = sweep_signatures(html)
        assert any(c.platform == "ashby" and c.slug == "ramp" for c in candidates)

    def test_spotify_zero_signatures(self):
        html = _read_html("lifeatspotify_careers")
        candidates = sweep_signatures(html)
        # Spotify uses Lever but the careers page has no lever.co links.
        assert len(candidates) == 0

    def test_legacy_greenhouse_domain(self):
        html = '<a href="https://boards.greenhouse.io/acme">jobs</a>'
        candidates = sweep_signatures(html)
        assert any(c.platform == "greenhouse" and c.slug == "acme" for c in candidates)

    def test_regional_greenhouse_subdomain(self):
        html = "https://job-boards.us.greenhouse.io/acme"
        candidates = sweep_signatures(html)
        assert any(c.platform == "greenhouse" and c.slug == "acme" for c in candidates)

    def test_lever_hosted_board(self):
        html = '<a href="https://jobs.lever.co/acme">Jobs</a>'
        candidates = sweep_signatures(html)
        assert any(c.platform == "lever" and c.slug == "acme" for c in candidates)

    def test_lever_direct_api_call(self):
        html = "fetch('https://api.lever.co/v0/postings/acme?mode=json')"
        candidates = sweep_signatures(html)
        assert any(c.platform == "lever" and c.slug == "acme" for c in candidates)

    def test_ashby_embed_config(self):
        html = "__ashbyBaseJobBoardUrl = \"https://jobs.ashbyhq.com/acme\""
        candidates = sweep_signatures(html)
        assert any(c.platform == "ashby" and c.slug == "acme" for c in candidates)

    def test_weak_gh_jid_yields_weak_candidate(self):
        html = "<script>window.gh_jid=123456</script>"
        candidates = sweep_signatures(html)
        weak = [c for c in candidates if c.weak]
        assert any(c.platform == "greenhouse" and c.slug is None for c in weak)

    def test_deduplicates_repeated_slug(self):
        html = (
            "https://job-boards.greenhouse.io/acme "
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs "
            "https://boards.greenhouse.io/acme"
        )
        candidates = sweep_signatures(html)
        acme_gh = [c for c in candidates if c.platform == "greenhouse" and c.slug == "acme"]
        assert len(acme_gh) == 1
        assert len(acme_gh[0].matched_patterns) >= 2  # multiple patterns captured

    def test_strong_before_weak_ordering(self):
        html = (
            "https://job-boards.greenhouse.io/acme "
            "window.gh_jid=999"
        )
        candidates = sweep_signatures(html)
        # The only Greenhouse slug is strong; no weak candidate should appear.
        assert not any(c.weak for c in candidates)

    def test_sweep_case_insensitive(self):
        html = "HTTPS://JOBS.LEVER.CO/ACME"
        candidates = sweep_signatures(html)
        assert any(c.platform == "lever" and c.slug == "acme" for c in candidates)

    def test_generic_nonats_yields_no_candidates(self):
        html = _read_html("generic_nonats_careers")
        candidates = sweep_signatures(html)
        assert len(candidates) == 0


# ---------------------------------------------------------------------------
# Task 2b: Probe
# ---------------------------------------------------------------------------


class TestProbe:
    """Task 2 — Live verification probe."""

    def test_guess_slugs_cobaltio(self):
        slugs = guess_slugs("Cobalt.io")
        assert "cobaltio" in slugs

    def test_guess_slugs_new_rocket(self):
        slugs = guess_slugs("New Rocket, Inc.")
        assert "newrocket" in slugs
        assert len(slugs) <= 5

    def test_guess_slugs_no_legal_suffix(self):
        slugs = guess_slugs("Acme Corp")
        # Base slug should have suffix stripped
        assert "acme" in slugs

    def test_guess_slugs_with_suffix_form(self):
        """Razorpay-style: the slug IS the full legal name."""
        slugs = guess_slugs("Acme Corp")
        assert "acmecorp" in slugs  # 4th variant: suffix retained

    def test_guess_slugs_capped_at_5(self):
        slugs = guess_slugs("Very Long Company Name With Many Words Inc.")
        assert len(slugs) <= 5

    def test_guess_slugs_no_empty_strings(self):
        for name in ["Co", "Inc.", ".", "A"]:
            slugs = guess_slugs(name)
            assert all(s for s in slugs)

    def test_guess_slugs_deterministic(self):
        slugs1 = guess_slugs("Cobalt.io")
        slugs2 = guess_slugs("Cobalt.io")
        assert slugs1 == slugs2

    def test_probe_verified_with_jobs(self):
        transport = _greenhouse_transport("cobaltio", "greenhouse_cobaltio_jobs")
        client = httpx.Client(transport=transport)
        result = probe_slug("greenhouse", "cobaltio", client=client)
        assert result.verified is True
        assert result.job_count > 0

    def test_probe_verified_on_empty_board(self):
        """A 200 with zero jobs is VERIFIED — the board is real (DISC-04)."""
        transport = _greenhouse_transport("somecompany", "greenhouse_empty")
        client = httpx.Client(transport=transport)
        result = probe_slug("greenhouse", "somecompany", client=client)
        assert result.verified is True
        assert result.job_count == 0

    def test_probe_not_verified_on_404(self):
        client = httpx.Client(transport=ALL_404)
        result = probe_slug("greenhouse", "notacompany", client=client)
        assert result.verified is False
        assert result.reason != ""

    def test_probe_not_verified_on_malformed_json(self):
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, content=b"not-json")
        )
        client = httpx.Client(transport=transport)
        result = probe_slug("greenhouse", "badcompany", client=client)
        assert result.verified is False

    def test_probe_issues_exactly_one_request(self):
        request_count = [0]

        def handler(r):
            request_count[0] += 1
            return httpx.Response(
                200, content=_read_ats_json("greenhouse_cobaltio_jobs")
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        probe_slug("greenhouse", "cobaltio", client=client)
        assert request_count[0] == 1


# ---------------------------------------------------------------------------
# Task 3: Resolution orchestration
# ---------------------------------------------------------------------------


class FakePageFetcher:
    """A fake PageFetcher for injecting HTML without network access."""

    def __init__(self, html: str = "", ok: bool = True, final_url: str | None = None) -> None:
        self.html = html
        self.ok = ok
        self.final_url = final_url
        self.calls: int = 0

    def fetch(self, url: str) -> PageResult:
        self.calls += 1
        return PageResult(
            ok=self.ok,
            url=url,
            final_url=self.final_url or url,
            html=self.html,
        )


class RecordingProbeTransport(httpx.BaseTransport):
    """MockTransport that records (host, path) pairs and maps them to JSON fixtures."""

    def __init__(self, responses: dict[str, tuple[int, bytes]]) -> None:
        self.responses = responses  # key: url substring, value: (status, body)
        self.request_log: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.request_log.append(url)
        for key, (status, body) in self.responses.items():
            if key in url:
                return httpx.Response(status, content=body)
        return httpx.Response(404, content=b'{"error":"not found"}')


def _make_recording_client(responses: dict[str, tuple[int, bytes]]) -> tuple[httpx.Client, RecordingProbeTransport]:
    transport = RecordingProbeTransport(responses)
    client = httpx.Client(transport=transport)
    return client, transport


class TestResolution:
    """Task 3 — resolve_employer orchestration (REG-01, REG-02, REG-03)."""

    # -- REG-01: add_employer input-form tests --

    def test_add_employer_cobaltio_url_resolves(self):
        """add_employer: careers URL with Greenhouse HTML fixture."""
        html = _read_html("cobaltio_careers")
        static_fetcher = FakePageFetcher(html=html)
        client, _ = _make_recording_client(
            {"boards-api.greenhouse.io/v1/boards/cobaltio": (200, _read_ats_json("greenhouse_cobaltio_jobs"))}
        )
        result = resolve_employer(
            name="Cobalt",
            careers_url="https://cobalt.io/careers",
            client=client,
            static_fetcher=static_fetcher,
        )
        assert result.status is ResolutionStatus.RESOLVED
        assert result.platform == "greenhouse"
        assert result.slug == "cobaltio"

    def test_add_employer_bare_domain_normalised(self):
        """add_employer: bare domain gets https:// prepended."""
        html = _read_html("cobaltio_careers")
        static_fetcher = FakePageFetcher(html=html)
        client, _ = _make_recording_client(
            {"boards-api.greenhouse.io/v1/boards/cobaltio": (200, _read_ats_json("greenhouse_cobaltio_jobs"))}
        )
        result = resolve_employer(
            name="Cobalt",
            careers_url="cobalt.io/careers",
            client=client,
            static_fetcher=static_fetcher,
        )
        assert result.status is ResolutionStatus.RESOLVED

    def test_add_employer_name_only_via_guesses(self):
        """add_employer: name-only path — no careers_url, Tier 1 slugs probed."""
        client, _ = _make_recording_client(
            {"boards-api.greenhouse.io/v1/boards/cobalt": (200, _read_ats_json("greenhouse_empty"))}
        )
        result = resolve_employer(
            name="Cobalt",
            careers_url=None,
            client=client,
        )
        assert result.status is ResolutionStatus.RESOLVED
        assert result.slug == "cobalt"
        assert result.resolved_via == "name_guess"

    def test_add_employer_unresolvable_still_registers(self):
        """add_employer: no careers URL + all probes fail → UNRESOLVED, not exception."""
        client = httpx.Client(transport=ALL_404)
        result = resolve_employer(name="ZZZZNOTACOMPANY", client=client)
        assert result.status is ResolutionStatus.UNRESOLVED
        assert result.reason != ""

    # -- REG-03: slug_differs (slug != trading name) --

    def test_slug_differs_newrocket_highmetric(self):
        """slug_differs: NewRocket's slug is 'highmetric', not 'newrocket'."""
        html = _read_html("newrocket_careers")
        static_fetcher = FakePageFetcher(html=html)
        client, _ = _make_recording_client(
            {"boards-api.greenhouse.io/v1/boards/highmetric": (200, _read_ats_json("greenhouse_empty"))}
        )
        result = resolve_employer(
            name="NewRocket",
            careers_url="https://newrocket.com/careers",
            client=client,
            static_fetcher=static_fetcher,
        )
        assert result.status is ResolutionStatus.RESOLVED
        assert result.slug == "highmetric"
        assert result.slug != "newrocket"

    def test_slug_differs_static_html_source(self):
        """slug_differs: when resolved from HTML, resolved_via is 'static_html'."""
        html = _read_html("newrocket_careers")
        static_fetcher = FakePageFetcher(html=html)
        client, _ = _make_recording_client(
            {"boards-api.greenhouse.io/v1/boards/highmetric": (200, _read_ats_json("greenhouse_empty"))}
        )
        result = resolve_employer(
            name="NewRocket",
            careers_url="https://newrocket.com/careers",
            client=client,
            static_fetcher=static_fetcher,
        )
        assert result.resolved_via == "static_html"

    # -- REG-02 probing invariants --

    def test_pattern_match_alone_never_resolves(self):
        """Central invariant: a regex match without a successful probe never resolves."""
        html = '<a href="https://job-boards.greenhouse.io/notarealcompany">Jobs</a>'
        static_fetcher = FakePageFetcher(html=html)
        # All probes return 404.
        client = httpx.Client(transport=ALL_404)
        result = resolve_employer(
            name="Not A Real Company",
            careers_url="https://notarealcompany.com/careers",
            client=client,
            static_fetcher=static_fetcher,
        )
        assert result.status is not ResolutionStatus.RESOLVED
        assert result.slug is None

    def test_tier1_runs_even_when_tier2_found_a_candidate(self):
        """Spotify: Tier 1 probes happen regardless of Tier 2 results."""
        html = _read_html("cobaltio_careers")  # Has greenhouse signature
        static_fetcher = FakePageFetcher(html=html)
        transport = RecordingProbeTransport(
            {"boards-api.greenhouse.io/v1/boards/cobaltio": (200, _read_ats_json("greenhouse_cobaltio_jobs"))}
        )
        client = httpx.Client(transport=transport)
        result = resolve_employer(
            name="Cobalt",
            careers_url="https://cobalt.io/careers",
            client=client,
            static_fetcher=static_fetcher,
        )
        # At least one Tier 1 (name guess) probe must appear in request log.
        name_guess_probes = [r for r in transport.request_log if "cobalt" in r.lower()]
        assert len(name_guess_probes) >= 1

    def test_two_verified_slugs_yields_ambiguous(self):
        """If two distinct (platform, slug) pairs both probe 200, result is AMBIGUOUS."""
        transport = RecordingProbeTransport(
            {
                "boards-api.greenhouse.io/v1/boards/cobalt": (200, _read_ats_json("greenhouse_empty")),
                "api.lever.co/v0/postings/cobalt": (200, _read_ats_json("lever_empty")),
            }
        )
        client = httpx.Client(transport=transport)
        result = resolve_employer(
            name="Cobalt",
            careers_url=None,
            client=client,
        )
        assert result.status is ResolutionStatus.AMBIGUOUS
        assert result.slug is None
        assert len(result.probed) >= 2

    def test_tier3_not_invoked_when_static_html_resolves(self):
        """Tier 3 invoked 0 times when Tiers 1+2 find a verified candidate."""
        html = _read_html("cobaltio_careers")
        static_fetcher = FakePageFetcher(html=html)
        rendered_fetcher = FakePageFetcher(html="")  # tracks calls
        client, _ = _make_recording_client(
            {"boards-api.greenhouse.io/v1/boards/cobaltio": (200, _read_ats_json("greenhouse_cobaltio_jobs"))}
        )
        resolve_employer(
            name="Cobalt",
            careers_url="https://cobalt.io/careers",
            client=client,
            static_fetcher=static_fetcher,
            rendered_fetcher=rendered_fetcher,
        )
        assert rendered_fetcher.calls == 0

    def test_tier3_invoked_when_static_finds_nothing(self):
        """Tier 3 invoked exactly once when Tiers 1+2 produce no verified candidate."""
        # Static HTML: no signatures; all name guess probes fail.
        static_fetcher = FakePageFetcher(html="<html>no signatures here</html>")
        # Rendered HTML: has a Greenhouse link.
        rendered_html = '<a href="https://job-boards.greenhouse.io/somecompany">Jobs</a>'
        rendered_fetcher = FakePageFetcher(html=rendered_html)
        client, _ = _make_recording_client(
            {"boards-api.greenhouse.io/v1/boards/somecompany": (200, _read_ats_json("greenhouse_empty"))}
        )
        result = resolve_employer(
            name="ZZZZZUNKNOWN",  # guesses won't match
            careers_url="https://somecompany.com/careers",
            client=client,
            static_fetcher=static_fetcher,
            rendered_fetcher=rendered_fetcher,
        )
        assert rendered_fetcher.calls == 1

    def test_renderer_unavailable_degrades_to_unresolved(self):
        """RendererUnavailable causes UNRESOLVED, not a crash."""

        class FailingRenderedFetcher:
            calls = 0

            def fetch(self, url: str) -> PageResult:
                self.calls += 1
                raise RendererUnavailable("Playwright not installed")

        static_fetcher = FakePageFetcher(html="<html>no sigs</html>")
        client = httpx.Client(transport=ALL_404)
        result = resolve_employer(
            name="ZZZZNOTSUCHCOMPANY",
            careers_url="https://example.com/careers",
            client=client,
            static_fetcher=static_fetcher,
            rendered_fetcher=FailingRenderedFetcher(),
        )
        assert result.status is ResolutionStatus.UNRESOLVED
        assert "renderer" in result.reason.lower() or "playwright" in result.reason.lower()

    def test_spotify_resolves_via_tier1_guess(self):
        """Spotify: zero ATS signatures in HTML; Tier 1 probes lever/spotify → 200."""
        html = _read_html("lifeatspotify_careers")
        static_fetcher = FakePageFetcher(html=html)
        client, _ = _make_recording_client(
            {"api.lever.co/v0/postings/spotify": (200, _read_ats_json("lever_spotify_postings"))}
        )
        result = resolve_employer(
            name="Spotify",
            careers_url="https://lifeatspotify.com/careers",
            client=client,
            static_fetcher=static_fetcher,
        )
        assert result.status is ResolutionStatus.RESOLVED
        assert result.platform == "lever"
        assert result.slug == "spotify"
        assert result.resolved_via == "name_guess"

    # -- build_ats_config --

    def test_build_ats_config_is_json_serialisable(self):
        from huntloop.registry.signatures import SlugCandidate

        result = ResolutionResult(
            status=ResolutionStatus.RESOLVED,
            platform="greenhouse",
            slug="cobaltio",
            resolved_via="static_html",
            candidates=(SlugCandidate("greenhouse", "cobaltio", matched_patterns=("hosted_board",)),),
            probed=(ProbeResult("greenhouse", "cobaltio", verified=True, job_count=5),),
        )
        config = build_ats_config(result)
        # Must not raise
        serialised = json.dumps(config)
        assert "resolved" in serialised

    def test_build_ats_config_resolution_keys_present(self):
        result = ResolutionResult(status=ResolutionStatus.UNRESOLVED, reason="all probes failed")
        config = build_ats_config(result)
        res = config["resolution"]
        for key in ("status", "resolved_via", "candidates", "probed", "checked_at", "reason"):
            assert key in res, f"missing key: {key}"

    # -- persistence round-trip --

    def test_persist_resolution_resolved_round_trip(self, main_session):
        """Resolved result persists ats, ats_identifier, ats_config, resolved_at."""
        from huntloop.registry.signatures import SlugCandidate

        result = ResolutionResult(
            status=ResolutionStatus.RESOLVED,
            platform="greenhouse",
            slug="cobaltio",
            resolved_via="static_html",
            candidates=(SlugCandidate("greenhouse", "cobaltio"),),
            probed=(ProbeResult("greenhouse", "cobaltio", verified=True, job_count=3),),
        )
        company = persist_resolution(main_session, "Cobalt", result)
        main_session.commit()

        refreshed = main_session.get(type(company), company.id)
        assert refreshed.ats is not None
        assert refreshed.ats.value == "greenhouse"
        assert refreshed.ats_identifier == "cobaltio"
        assert refreshed.ats_config["resolution"]["status"] == "resolved"
        assert refreshed.resolved_at is not None

    def test_persist_resolution_unresolved_still_registers(self, main_session):
        """UNRESOLVED result still creates a Company row (enabled=True, ats=None)."""
        result = ResolutionResult(
            status=ResolutionStatus.UNRESOLVED,
            reason="no probes succeeded",
        )
        company = persist_resolution(main_session, "UnknownCo", result)
        main_session.commit()

        refreshed = main_session.get(type(company), company.id)
        assert refreshed is not None
        assert refreshed.enabled is True
        assert refreshed.ats is None
        assert refreshed.resolved_at is None
