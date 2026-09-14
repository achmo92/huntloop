"""SSRF URL-guard tests (T-04-03, plan 04-27).

The guard is deliberately best-effort, not a hardened egress proxy: literal
loopback/link-local (cloud metadata)/private/unique-local/multicast/reserved/
unspecified addresses and credential-bearing URLs are rejected synchronously,
and a hostname is resolved with ``getaddrinfo`` and rejected when ANY returned
address is non-public. An unverifiable hostname (resolution failure) is allowed
because this is a single-user LAN app: the documented TOCTOU residual is
accepted rather than eliminated with connection pinning.

Task 1 covers the guard function itself; the fetch-site enforcement tests
(StaticPageFetcher/redirect cap, crawl, RenderedPageFetcher) live alongside the
enforcement change in Task 2.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from huntloop.discovery.crawl.careers import crawl_careers
from huntloop.discovery.fetch.page import (
    PageResult,
    RenderedPageFetcher,
    StaticPageFetcher,
)
from huntloop.fetching.url_guard import UnsafeUrlError, assert_fetch_url_allowed

# Every one of these must be rejected before a byte of network traffic is spent.
BLOCKED_URLS = [
    "http://127.0.0.1/",  # loopback
    "http://localhost/",  # loopback by name
    "http://169.254.169.254/latest/meta-data/",  # link-local cloud metadata
    "http://10.1.2.3/",  # RFC1918
    "http://192.168.1.10/",  # RFC1918
    "http://172.16.5.5/",  # RFC1918
    "http://[::1]/",  # IPv6 loopback
    "http://0.0.0.0/",  # unspecified
    "http://[fd00::1]/",  # IPv6 unique-local
    "http://224.0.0.1/",  # multicast
    "http://user:pass@example.com/",  # URL credentials
    "file:///etc/passwd",  # non-http scheme
    "ftp://example.com/",  # non-http scheme
    "https:///no-host",  # no host at all
]


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_blocked_urls_are_rejected(url):
    with pytest.raises(UnsafeUrlError):
        assert_fetch_url_allowed(url)


@pytest.mark.parametrize("url", ["https://example.com/", "http://example.com/path"])
def test_public_urls_are_allowed(url):
    assert assert_fetch_url_allowed(url) == url


def test_allow_private_accepts_loopback_and_metadata():
    assert (
        assert_fetch_url_allowed("http://127.0.0.1/v1", allow_private=True)
        == "http://127.0.0.1/v1"
    )
    assert (
        assert_fetch_url_allowed("http://169.254.169.254/", allow_private=True)
        == "http://169.254.169.254/"
    )


def test_hostname_resolving_to_metadata_is_rejected():
    resolver = lambda host, port: [(0, 0, 0, "", ("169.254.169.254", 0))]
    with pytest.raises(UnsafeUrlError):
        assert_fetch_url_allowed("https://internal.example/", resolver=resolver)


def test_resolver_returning_public_address_is_accepted():
    resolver = lambda host, port: [("93.184.216.34", 0)]
    assert (
        assert_fetch_url_allowed("https://public.example/", resolver=resolver)
        == "https://public.example/"
    )


def test_unverifiable_hostname_is_allowed():
    def resolver(host, port):
        raise socket.gaierror("name resolution failed")

    assert (
        assert_fetch_url_allowed("https://nowhere.example/", resolver=resolver)
        == "https://nowhere.example/"
    )


# ---------------------------------------------------------------------------
# Task 2: enforcement at every user-URL fetch site (T-04-03)
# ---------------------------------------------------------------------------


def _recording_static_fetcher(handler):
    """StaticPageFetcher over a MockTransport that records requested URLs."""
    calls: list[str] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(recording_handler))
    return StaticPageFetcher(client=client), calls


def test_static_fetcher_blocks_metadata_without_requesting_it():
    fetcher, calls = _recording_static_fetcher(
        lambda request: httpx.Response(200, content=b"<html/>")
    )

    result = fetcher.fetch("http://169.254.169.254/latest/meta-data/")

    assert result.ok is False
    assert calls == []


def test_static_fetcher_refuses_redirect_to_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    fetcher, calls = _recording_static_fetcher(handler)

    result = fetcher.fetch("https://example.com/start")

    assert result.ok is False
    assert all("169.254.169.254" not in url for url in calls)


def test_static_fetcher_caps_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        # A redirect loop that never leaves the same public hostname.
        return httpx.Response(302, headers={"Location": "https://example.com/loop"})

    fetcher, calls = _recording_static_fetcher(handler)

    result = fetcher.fetch("https://example.com/start")

    assert result.ok is False
    # Bounded: the cap stops the loop rather than following it forever.
    assert len(calls) <= 7


class _RecordingFetcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch(self, url: str) -> PageResult:
        self.calls.append(url)
        return PageResult(ok=False, url=url, error="fetcher must not be called")


def test_crawl_careers_blocks_metadata_base_url():
    fetcher = _RecordingFetcher()

    result = crawl_careers(fetcher, "http://169.254.169.254/")

    assert result.pages == ()
    assert fetcher.calls == []


def test_rendered_fetcher_blocks_metadata_before_playwright():
    # No Playwright installed: the guard must run BEFORE the lazy import, so
    # this returns a blocked PageResult instead of raising RendererUnavailable.
    result = RenderedPageFetcher().fetch("http://169.254.169.254/")

    assert result.ok is False
