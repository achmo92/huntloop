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

import pytest

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
