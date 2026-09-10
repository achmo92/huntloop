"""Deterministic dedup keys and URL normalization for job listings.

DISC-06 depends entirely on this function being a pure, deterministic map
from listing identity to key. Anything time-varying, run-varying, or
content-varying in here silently doubles the tracker on every run, and the
damage compounds — by the time a user notices, reconciliation is manual.
Hence: no clock, no run id, no title, no random. The path case is preserved
deliberately, but the scheme and host are normalised because an employer
upgrading to https or dropping the www must not fork every row.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from huntloop.discovery.ats.base import RawListing

MAX_DEDUP_KEY_CHARS = 240      # jobs.dedup_key is a unique-indexed text column; Postgres btree
                               # entries are bounded, and a pathological URL must not break the
                               # index. Beyond this the URL branch is truncated + sha suffixed.
TRACKING_PARAMS: frozenset[str] = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gh_src", "ref", "source", "lever-origin", "lever-source", "fbclid", "gclid",
})


class DedupKeyError(ValueError):
    """A listing carried neither an external id nor a URL and cannot be keyed."""


def company_slug(name: str) -> str:
    """Lowercase, strip, remove every non-alphanumeric character."""
    return re.sub(r"[^a-z0-9]", "", name.lower().strip())


def normalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = "https"                         # scheme-insensitive: http/https are the same posting
    host = parsed.netloc.lower().removeprefix("www.").rstrip(".")
    path = parsed.path.rstrip("/") or "/"    # path case preserved: some ATSs are case-sensitive
    params = sorted(
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS
    )
    return urlunsplit((scheme, host, path, urlencode(params), ""))


def compute_dedup_key(company_name: str, listing: RawListing) -> str:
    if listing.external_id:
        return f"{company_slug(company_name)}:{listing.external_id}"
        
    if listing.url:
        key = f"url:{normalize_url(listing.url)}"
        if len(key) > MAX_DEDUP_KEY_CHARS:
            digest = hashlib.sha256(key.encode()).hexdigest()[:16]
            key = f"{key[: MAX_DEDUP_KEY_CHARS - 17]}#{digest}"
        return key
        
    raise DedupKeyError(f"listing {listing.title!r} has neither external_id nor url")
