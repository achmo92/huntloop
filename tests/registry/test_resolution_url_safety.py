"""resolve_employer must reject unsafe user-supplied careers URLs (T-04-03).

The guard runs immediately after input normalisation and before any Tier-2 or
Tier-3 fetch, so a metadata/loopback URL is refused with no HTTP request at
all (the recording transport proves zero traffic).
"""

from __future__ import annotations

import httpx
import pytest

from huntloop.fetching.url_guard import UnsafeUrlError
from huntloop.registry.resolve import resolve_employer


def _recording_transport(calls: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404, content=b'{"error": "not found"}')

    return httpx.MockTransport(handler)


def test_resolve_rejects_metadata_careers_url():
    calls: list[str] = []
    client = httpx.Client(transport=_recording_transport(calls))

    with pytest.raises(UnsafeUrlError):
        resolve_employer(
            name="Evil",
            careers_url="http://169.254.169.254/latest/meta-data/",
            client=client,
        )

    assert calls == []


def test_resolve_rejects_loopback_careers_url():
    calls: list[str] = []
    client = httpx.Client(transport=_recording_transport(calls))

    with pytest.raises(UnsafeUrlError):
        resolve_employer(name="Evil", careers_url="http://127.0.0.1/", client=client)

    assert calls == []
