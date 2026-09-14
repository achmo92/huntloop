"""Request-boundary hardening for the private-network, no-auth API (T-04-02).

The API is intentionally unauthenticated (PROJECT.md private-network trust
model), so the browser is the boundary: a page on another origin, or a
DNS-rebinding domain, must not be able to drive state changes or enumerate
the API. These are boundary checks, NOT an auth system — no credentials,
sessions, or tokens are introduced.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class SameOriginMiddleware:
    """Reject browser-origin state changes that do not match the request Host.

    Fail-closed for POST/PUT/PATCH/DELETE. A request whose Origin (or, when
    Origin is absent, Referer) netloc differs from the request's Host header
    is rejected 403. A request with NEITHER header is a non-browser client
    (curl, the CLI, tests) and is allowed — documented choice: browsers always
    attach Origin to cross-origin state-changing requests, so enforcement
    cannot be evaded by simply omitting it in a browser.
    """

    def __init__(self, app: ASGIApp, *, allowed_origins: list[str] | None = None) -> None:
        self.app = app
        self.allowed_origins = frozenset(
            origin.rstrip("/").lower() for origin in (allowed_origins or []) if origin.strip()
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = (headers.get("host") or "").lower()
        candidate = headers.get("origin") or headers.get("referer")
        if candidate is None:
            await self.app(scope, receive, send)  # non-browser client
            return
        if not self._allowed(candidate, host):
            await JSONResponse(
                {"detail": "Cross-origin request rejected."}, status_code=403
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _allowed(self, value: str, host: str) -> bool:
        raw = value.strip().lower()
        if raw == "null":
            return False  # sandboxed/opaque origin: no legitimate same-origin call
        parsed = urlsplit(raw)
        netloc = parsed.netloc.lower()
        if not netloc:
            return False
        origin = f"{parsed.scheme}://{netloc}"
        if origin in self.allowed_origins:
            return True
        return netloc == host


class LANTrustedHostMiddleware(TrustedHostMiddleware):
    """TrustedHostMiddleware that also accepts IP-literal Hosts.

    DNS rebinding targets a *hostname*: the attacker page's origin is a domain
    it controls, so every request carries that domain in Host; a bare IP-literal
    Host can only be reached by the victim deliberately navigating to the LAN
    address, which is not a rebinding attack. Accepting IP literals is therefore
    what keeps UI-01 ("any device on the network", e.g. http://192.168.1.50:8000)
    working without requiring the operator to list their LAN IP, while
    Host=dns.evil.example is still rejected 400.

    HUNTLOOP_ALLOWED_HOSTS (+ localhost/127.0.0.1/testserver defaults) is the
    hostname allowlist for reverse-proxy / DNS-name access. See .env.example.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            host = Headers(scope=scope).get("host") or ""
            bare = host[1 : host.index("]")] if host.startswith("[") else host.split(":")[0]
            try:
                ipaddress.ip_address(bare)
            except ValueError:
                pass
            else:
                await self.app(scope, receive, send)
                return
        await super().__call__(scope, receive, send)
