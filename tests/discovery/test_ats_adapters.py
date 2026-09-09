import json
import pytest
import httpx
from huntloop.discovery.ats.base import (
    FetchStatus, ErrorKind, RawListing, FetchResult,
    make_client, fetch_json, classify_response
)

class TestFetchContract:
    def test_zero_vs_error_200_ok(self):
        def parser(payload):
            return [RawListing("1", "url1", "title1"), RawListing("2", "url2", "title2")]
        res = classify_response(200, {"jobs": [1, 2]}, parser)
        assert res.status == FetchStatus.OK
        assert len(res.listings) == 2

    def test_zero_vs_error_200_empty_is_not_error(self):
        def parser(payload):
            return []
        res = classify_response(200, {"jobs": []}, parser)
        assert res.status == FetchStatus.EMPTY
        assert res.listings == []
        assert res.error_kind is None
        assert res.is_confirmed_empty is True

    def test_zero_vs_error_429_is_rate_limited(self):
        def handler(request):
            return httpx.Response(429)
        client = make_client(transport=httpx.MockTransport(handler))
        http_status, payload, error_kind, msg = fetch_json("http://test", client=client)
        # Assuming adapters will do: if error_kind: return FetchResult(status=FetchStatus.ERROR, ...)
        assert http_status == 429
        assert error_kind == ErrorKind.RATE_LIMITED

    def test_zero_vs_error_500_is_http_error(self):
        calls = 0
        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(500)
        client = make_client(transport=httpx.MockTransport(handler))
        http_status, payload, error_kind, msg = fetch_json("http://test", client=client)
        assert http_status == 500
        assert error_kind == ErrorKind.HTTP_ERROR
        assert calls == 3

    def test_zero_vs_error_timeout_is_timeout(self):
        def handler(request):
            raise httpx.TimeoutException("timeout")
        client = make_client(transport=httpx.MockTransport(handler))
        http_status, payload, error_kind, msg = fetch_json("http://test", client=client)
        assert error_kind == ErrorKind.TIMEOUT
        assert http_status is None

    def test_zero_vs_error_connect_is_connection_error(self):
        def handler(request):
            raise httpx.ConnectError("connect error")
        client = make_client(transport=httpx.MockTransport(handler))
        http_status, payload, error_kind, msg = fetch_json("http://test", client=client)
        assert error_kind == ErrorKind.CONNECTION_ERROR
        assert http_status is None

    def test_zero_vs_error_malformed_200_is_parse_error(self):
        def handler(request):
            return httpx.Response(200, content=b"not json at all")
        client = make_client(transport=httpx.MockTransport(handler))
        http_status, payload, error_kind, msg = fetch_json("http://test", client=client)
        assert http_status == 200
        assert error_kind == ErrorKind.PARSE_ERROR

    def test_zero_vs_error_wrong_shape_is_parse_error(self):
        def parser(payload):
            if "jobs" not in payload:
                raise ValueError("Missing jobs")
            return []
        res = classify_response(200, {"unexpected": 1}, parser)
        assert res.status == FetchStatus.ERROR
        assert res.error_kind == ErrorKind.PARSE_ERROR
        assert res.is_confirmed_empty is False

    def test_zero_vs_error_is_confirmed_empty(self):
        assert FetchResult(status=FetchStatus.EMPTY).is_confirmed_empty is True
        assert FetchResult(status=FetchStatus.OK).is_confirmed_empty is False
        assert FetchResult(status=FetchStatus.ERROR).is_confirmed_empty is False

    def test_fetch_json_does_not_retry_404(self):
        calls = 0
        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(404)
        client = make_client(transport=httpx.MockTransport(handler))
        http_status, payload, error_kind, msg = fetch_json("http://test", client=client)
        assert calls == 1
        assert error_kind == ErrorKind.HTTP_ERROR

class TestGreenhouse:
    def test_fetch_greenhouse_ok(self, ats_fixture):
        from huntloop.discovery.ats.greenhouse import ADAPTER
        def handler(request):
            assert "content=true" in str(request.url)
            assert "boards-api.greenhouse.io/v1/boards" in str(request.url)
            assert "authorization" not in request.headers
            return httpx.Response(200, json=ats_fixture("greenhouse_cobaltio_jobs"))
        
        client = make_client(transport=httpx.MockTransport(handler))
        res = ADAPTER.fetch("cobaltio", client=client)
        
        assert res.status == FetchStatus.OK
        assert len(res.listings) == 3
        assert all(l.title and l.url for l in res.listings)
    
    def test_description_greenhouse_unescapes_html(self, ats_fixture):
        from huntloop.discovery.ats.greenhouse import ADAPTER
        from huntloop.discovery.ats.base import has_usable_description
        def handler(request):
            return httpx.Response(200, json=ats_fixture("greenhouse_cobaltio_jobs"))
        client = make_client(transport=httpx.MockTransport(handler))
        res = ADAPTER.fetch("cobaltio", client=client)
        
        l0 = res.listings[0]
        assert "<" in l0.description_html
        assert "&lt;" not in l0.description_html
        assert has_usable_description(l0) is True
    
    def test_description_greenhouse_empty_local(self, ats_fixture):
        from huntloop.discovery.ats.greenhouse import ADAPTER
        from huntloop.discovery.ats.base import has_usable_description
        
        data = ats_fixture("greenhouse_cobaltio_jobs")
        data["jobs"][0]["content"] = ""
        def handler(request):
            return httpx.Response(200, json=data)
        
        client = make_client(transport=httpx.MockTransport(handler))
        res = ADAPTER.fetch("cobaltio", client=client)
        assert res.status == FetchStatus.OK
        assert has_usable_description(res.listings[0]) is False

    def test_zero_vs_error_greenhouse_empty(self, ats_fixture):
        from huntloop.discovery.ats.greenhouse import ADAPTER
        def handler(request):
            return httpx.Response(200, json=ats_fixture("greenhouse_empty"))
        client = make_client(transport=httpx.MockTransport(handler))
        res = ADAPTER.fetch("empty", client=client)
        assert res.status == FetchStatus.EMPTY

class TestLever:
    def test_fetch_lever_ok(self, ats_fixture):
        from huntloop.discovery.ats.lever import ADAPTER
        def handler(request):
            assert "mode=json" in str(request.url)
            assert "authorization" not in request.headers
            return httpx.Response(200, json=ats_fixture("lever_spotify_postings"))
        
        client = make_client(transport=httpx.MockTransport(handler))
        res = ADAPTER.fetch("spotify", client=client)
        assert res.status == FetchStatus.OK
        assert len(res.listings) > 0
        l0 = res.listings[0]
        assert l0.description_plain
        assert l0.country_code
        assert l0.posted_at.year > 2000

    def test_description_lever_usable(self, ats_fixture):
        from huntloop.discovery.ats.lever import ADAPTER
        from huntloop.discovery.ats.base import has_usable_description
        def handler(request):
            return httpx.Response(200, json=ats_fixture("lever_spotify_postings"))
        client = make_client(transport=httpx.MockTransport(handler))
        res = ADAPTER.fetch("spotify", client=client)
        assert all(has_usable_description(l) for l in res.listings)

    def test_zero_vs_error_lever_empty(self, ats_fixture):
        from huntloop.discovery.ats.lever import ADAPTER
        def handler(request):
            return httpx.Response(200, json=ats_fixture("lever_empty"))
        client = make_client(transport=httpx.MockTransport(handler))
        res = ADAPTER.fetch("empty", client=client)
        assert res.status == FetchStatus.EMPTY

class TestAshby:
    def test_fetch_ashby_ok(self, ats_fixture):
        from huntloop.discovery.ats.ashby import ADAPTER
        def handler(request):
            assert "includeCompensation=true" in str(request.url)
            assert "authorization" not in request.headers
            return httpx.Response(200, json=ats_fixture("ashby_ramp_jobs"))
        
        client = make_client(transport=httpx.MockTransport(handler))
        res = ADAPTER.fetch("ramp", client=client)
        assert res.status == FetchStatus.OK
        l0 = res.listings[0]
        assert l0.description_html
        assert l0.description_plain
        assert l0.is_remote is not None
        assert l0.workplace_type

    def test_description_ashby_usable(self, ats_fixture):
        from huntloop.discovery.ats.ashby import ADAPTER
        from huntloop.discovery.ats.base import has_usable_description
        def handler(request):
            return httpx.Response(200, json=ats_fixture("ashby_ramp_jobs"))
        client = make_client(transport=httpx.MockTransport(handler))
        res = ADAPTER.fetch("ramp", client=client)
        assert all(has_usable_description(l) for l in res.listings)

    def test_zero_vs_error_ashby_empty(self, ats_fixture):
        from huntloop.discovery.ats.ashby import ADAPTER
        def handler(request):
            return httpx.Response(200, json=ats_fixture("ashby_empty"))
        client = make_client(transport=httpx.MockTransport(handler))
        res = ADAPTER.fetch("empty", client=client)
        assert res.status == FetchStatus.EMPTY

class TestAdapterRegistry:
    def test_get_adapter(self):
        from huntloop.discovery.ats.registry import get_adapter, UnsupportedPlatform
        assert get_adapter("greenhouse").platform == "greenhouse"
        assert get_adapter("lever").platform == "lever"
        assert get_adapter("ashby").platform == "ashby"
        
        with pytest.raises(UnsupportedPlatform):
            get_adapter("workday")
