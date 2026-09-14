"""SSRF guard for user-supplied URLs (T-04-03).

A user can type a careers URL, so nothing may fetch a target that points at the
host's own network. Best-effort, not a hardened egress proxy: hostnames are
resolved with ``getaddrinfo`` and rejected if any returned address is
non-public. DNS can rebind between this check and the actual connection
(TOCTOU); for a single-user LAN app that residual risk is accepted and
documented rather than eliminated with connection pinning.

Nothing here adds authentication — this is boundary hardening layered on the
private-network, no-auth design in PROJECT.md.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class UnsafeUrlError(ValueError):
    """Raised when a URL may not be fetched (SSRF guard)."""


def _is_blocked(ip) -> bool:
    """True for any address class that can reach the host's own network.

    Covers loopback, link-local (169.254.0.0/16 — cloud metadata), RFC1918
    private, IPv6 unique-local (fc00::/7), multicast, IANA-reserved,
    unspecified (0.0.0.0) and IPv6 site-local addresses.
    """
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or getattr(ip, "is_site_local", False)
    )


def _address_of(info) -> str | None:
    """Extract the address string from a resolver entry.

    Accepts the real ``getaddrinfo`` 5-tuple shape
    (``(family, type, proto, canonname, sockaddr)``) and a bare sockaddr-shaped
    ``("93.184.216.34", 0)`` so an injected test resolver can use either form
    without depending on the platform's tuple layout.
    """
    if isinstance(info, str):
        return info
    if not isinstance(info, (tuple, list)) or not info:
        return None
    if len(info) >= 5 and isinstance(info[4], (tuple, list)) and info[4]:
        return info[4][0]
    return info[0]


def assert_fetch_url_allowed(
    url: str, *, allow_private: bool = False, resolver=None
) -> str:
    """Return ``url`` when it may be fetched, else raise :class:`UnsafeUrlError`.

    ``allow_private=True`` skips the address checks entirely (the explicit
    ``HUNTLOOP_ALLOW_PRIVATE_ENDPOINT`` escape hatch). ``resolver`` is
    injectable for tests and defaults to :func:`socket.getaddrinfo`.
    """
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"unsupported scheme: {parsed.scheme or '(none)'}")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("credentials in the URL are not allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL has no host")
    if allow_private:
        return url

    # A literal IP needs no resolution and must be checked directly.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked(literal):
            raise UnsafeUrlError(f"blocked private/loopback address: {host}")
        return url

    resolve = resolver or socket.getaddrinfo
    try:
        infos = resolve(host, None)
    except (socket.gaierror, OSError):
        # Unverifiable now -> best-effort allow. Documented TOCTOU residual.
        return url

    for info in infos:
        address = _address_of(info)
        if address is None:
            continue
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if _is_blocked(ip):
            raise UnsafeUrlError(f"{host} resolves to a blocked address ({ip})")
    return url
