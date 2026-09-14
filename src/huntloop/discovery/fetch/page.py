"""The Tier 2 (static) and Tier 3 (rendered) resolution passes run the identical signature
sweep; the only difference is which fetcher produced the HTML. Keeping that behind one
Protocol means the expensive rendering path can never accidentally acquire behaviour the
cheap path lacks. 02-CONTEXT.md rules out Firecrawl for this role; Playwright is the
rendering backend and is an optional runtime dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin

import httpx

from huntloop.discovery.ats.base import DEFAULT_HEADERS, DEFAULT_TIMEOUT
from huntloop.fetching.url_guard import UnsafeUrlError, assert_fetch_url_allowed

# Maximum HTML body retained in memory per fetch.  5 MiB is enough for the largest
# server-rendered careers page observed in research (Ramp, ~4.2 MiB), small enough
# that a pathological page cannot exhaust memory during a fan-out run.
MAX_HTML_BYTES = 5 * 1024 * 1024

# T-04-03: redirects are followed manually so every hop can be re-validated
# against the SSRF guard. A small cap keeps a redirect loop bounded.
_MAX_REDIRECTS = 5

# Descriptive user-agent used by BOTH fetchers — employer sites should see why they
# are being visited. No Authorization header. No cookies. No credential of any kind
# is ever attached: this fetcher visits arbitrary third-party employer sites.
USER_AGENT = "HuntLoop/0.2 (+https://github.com/huntloop; personal job discovery)"


class RendererUnavailable(RuntimeError):
    """Playwright or its browser binary is not installed."""


@dataclass(frozen=True)
class PageResult:
    ok: bool
    url: str
    final_url: str | None = None
    status_code: int | None = None
    html: str = ""
    rendered: bool = False
    truncated: bool = False
    error: str | None = None


class PageFetcher(Protocol):
    """Common interface shared by StaticPageFetcher and RenderedPageFetcher."""

    def fetch(self, url: str) -> PageResult: ...


def _get_with_safe_redirects(client: httpx.Client, url: str) -> httpx.Response:
    """GET ``url``, following redirects manually with the guard on every hop.

    ``follow_redirects=False`` per request overrides a client built with
    ``follow_redirects=True`` (the injected test client), so the guard sees each
    intermediate URL before it is requested — a redirect to a metadata/private
    address is refused and that address is never contacted. Raises
    :class:`~huntloop.fetching.url_guard.UnsafeUrlError` on a blocked hop or
    when the hop cap is exceeded.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        assert_fetch_url_allowed(current)
        response = client.get(current, follow_redirects=False)
        if response.is_redirect and "location" in response.headers:
            current = urljoin(str(response.url), response.headers["location"])
            continue
        return response
    raise UnsafeUrlError("too many redirects")


class StaticPageFetcher:
    """Synchronous httpx-based HTML fetcher (Tier 2 pass).

    The injected-client design is intentional: it is what makes ``httpx.MockTransport``
    testing possible without any network access.  Do NOT construct the client inside
    ``fetch`` — doing so would break tests and prevent connection pooling in production.
    """

    def __init__(self, client: httpx.Client | None = None) -> None:
        if client is None:
            client = httpx.Client(
                timeout=DEFAULT_TIMEOUT,
                headers={**DEFAULT_HEADERS, "User-Agent": USER_AGENT},
                follow_redirects=True,
                # No Authorization header, no cookies, no credentials.
            )
        self._client = client

    def fetch(self, url: str) -> PageResult:
        try:
            response = _get_with_safe_redirects(self._client, url)
        except UnsafeUrlError as exc:
            return PageResult(
                ok=False,
                url=url,
                error=f"blocked unsafe URL: {exc}",
            )
        except httpx.HTTPError as exc:
            return PageResult(
                ok=False,
                url=url,
                error=f"{type(exc).__name__}: {exc}",
            )

        if not response.is_success:
            return PageResult(
                ok=False,
                url=url,
                final_url=str(response.url),
                status_code=response.status_code,
                error=f"HTTP {response.status_code}",
            )

        raw_bytes = response.content
        truncated = len(raw_bytes) > MAX_HTML_BYTES
        html = response.text[:MAX_HTML_BYTES]

        return PageResult(
            ok=True,
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            html=html,
            rendered=False,
            truncated=truncated,
        )


class RenderedPageFetcher:
    """Playwright Chromium fetcher (Tier 3 pass).

    Playwright is an OPTIONAL runtime dependency.  Importing it is deferred to inside
    ``fetch`` so that the entire test suite can run with no browser installed.  When
    Playwright is absent, ``RendererUnavailable`` is raised with the install command —
    the caller can catch it and degrade to UNRESOLVED rather than crashing.
    """

    def __init__(self, *, timeout_ms: int = 20000, wait_until: str = "networkidle") -> None:
        self.timeout_ms = timeout_ms
        self.wait_until = wait_until

    def fetch(self, url: str) -> PageResult:
        # T-04-03: guard BEFORE the lazy Playwright import, so an unsafe URL is
        # refused with a normal PageResult even when the browser is absent.
        try:
            assert_fetch_url_allowed(url)
        except UnsafeUrlError as exc:
            return PageResult(ok=False, url=url, error=f"blocked unsafe URL: {exc}")

        # Lazy import — Playwright is optional.  The whole test suite runs without it.
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError as exc:
            raise RendererUnavailable(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            ) from exc

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    context = browser.new_context(user_agent=USER_AGENT)
                    try:
                        page = context.new_page()
                        try:
                            page.goto(url, wait_until=self.wait_until, timeout=self.timeout_ms)
                            html = page.content()
                            final_url = page.url
                            # T-04-03: Playwright cannot intercept per-hop
                            # redirects, so the final URL is the boundary and
                            # this is best-effort. A blocked final URL is
                            # reported as a non-ok PageResult below.
                            assert_fetch_url_allowed(final_url)
                        except Exception as exc:
                            error_msg = str(exc)
                            if "timeout" in error_msg.lower():
                                return PageResult(
                                    ok=False,
                                    url=url,
                                    error=f"timeout after {self.timeout_ms}ms",
                                )
                            return PageResult(
                                ok=False,
                                url=url,
                                error=f"{type(exc).__name__}: {exc}",
                            )
                        finally:
                            page.close()
                    finally:
                        context.close()
                finally:
                    browser.close()
        except RendererUnavailable:
            raise
        except Exception as exc:
            # Catch "Executable doesn't exist" and similar Playwright setup errors.
            error_msg = str(exc)
            if "executable" in error_msg.lower() or "browser" in error_msg.lower():
                raise RendererUnavailable(
                    "Playwright is not installed. Run: pip install playwright && playwright install chromium"
                ) from exc
            return PageResult(
                ok=False,
                url=url,
                error=f"{type(exc).__name__}: {exc}",
            )

        truncated = len(html.encode()) > MAX_HTML_BYTES
        html_capped = html[:MAX_HTML_BYTES]

        return PageResult(
            ok=True,
            url=url,
            final_url=final_url,
            html=html_capped,
            rendered=True,
            truncated=truncated,
        )
