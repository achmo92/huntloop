"""Slug-guess generation and live verification probe (REG-02, REG-03).

Live probing is the only thing that promotes a slug candidate to RESOLVED.
A regex match or a name guess is a CANDIDATE.  See ``resolve_employer`` in
``resolve.py`` for the full three-tier orchestration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from huntloop.discovery.ats.base import FetchStatus
from huntloop.discovery.ats.registry import get_adapter

# ---------------------------------------------------------------------------
# guess_slugs
# ---------------------------------------------------------------------------

# Common legal suffixes that appear in company names but rarely in ATS slugs.
SUFFIXES = (
    "inc",
    "inc.",
    "llc",
    "ltd",
    "ltd.",
    "limited",
    "corp",
    "corporation",
    "co",
    "gmbh",
    "plc",
    "pvt",
    "private",
    "sa",
    "bv",
    "ag",
)

# Match a legal suffix as a trailing whole word (case-insensitive).
_SUFFIX_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(s) for s in SUFFIXES) + r")\s*$",
    re.IGNORECASE,
)


def _strip_legal(name: str) -> str:
    """Remove a trailing legal suffix from a company name."""
    return _SUFFIX_RE.sub("", name).strip()


def guess_slugs(name: str) -> tuple[str, ...]:
    """Produce ATS slug guesses from a company name.

    These are GUESSES.  Two live-reproduced cases — Razorpay's slug is
    ``razorpaysoftwareprivatelimited``, NewRocket's is ``highmetric`` — show a
    trading-name guess fails routinely.  Nothing here may be persisted as
    ``ats_identifier`` without ``probe_slug`` confirming it.

    Returned tuple is deterministic, deduplicated, empty strings excluded, capped
    at 5 candidates.  Ordering is stable (most-likely candidates first):

    1. ``base`` — lowercased, legal suffix stripped, non-alphanumeric removed.
    2. Hyphenated variant — spaces → ``-``.
    3. Base with a trailing ``io`` / ``hq`` / ``ai`` stripped (if ≥ 3 chars remain).
    4. ``name_with_suffix`` — base computed WITHOUT stripping the legal suffix,
       i.e. the Razorpay / ``razorpaysoftwareprivatelimited`` form.
    5. First word of the stripped base, when more than one word was present.
    """
    stripped = _strip_legal(name).strip()

    # 1. base: alphanumeric only, spaces collapsed out
    base = re.sub(r"[^a-z0-9]", "", stripped.lower())
    # 2. hyphenated: spaces → -, other non-alphanumeric removed
    hyphenated = re.sub(r"[^a-z0-9-]", "", stripped.lower().replace(" ", "-")).strip("-")

    # 3. Strip trailing io/hq/ai if ≥ 3 chars remain
    stripped_suffix = None
    for tld in ("io", "hq", "ai"):
        if base.endswith(tld) and len(base) - len(tld) >= 3:
            stripped_suffix = base[: -len(tld)]
            break

    # 4. Name with legal suffix retained (raw alphanumeric of the original name)
    name_with_suffix = re.sub(r"[^a-z0-9]", "", name.lower())

    # 5. First word of stripped base
    words = re.findall(r"[a-z0-9]+", stripped.lower())
    first_word = words[0] if len(words) > 1 else None

    candidates: list[str] = []
    seen: set[str] = set()
    for candidate in filter(
        None,
        [base, hyphenated, stripped_suffix, name_with_suffix, first_word],
    ):
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
        if len(candidates) == 5:
            break

    return tuple(candidates)


# ---------------------------------------------------------------------------
# probe_slug
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    platform: str
    slug: str
    verified: bool
    job_count: int = 0
    reason: str = ""


def probe_slug(platform: str, slug: str, *, client: httpx.Client) -> ProbeResult:
    """Issue a live API call to verify that ``slug`` is a real board on ``platform``.

    A confirmed-empty board (200, well-formed, zero postings) is a VERIFIED board.
    Treating empty as unverified would un-resolve every employer that happens to
    have no openings on registration day, and would then re-run the whole three-tier
    pipeline on every subsequent run.

    One HTTP request per call.  No retry — retries belong in the adapter's ``fetch``
    method which already uses tenacity.
    """
    adapter = get_adapter(platform)
    result = adapter.fetch(slug, client=client)

    # FetchStatus.EMPTY means "HTTP 200, zero jobs" — the board is real (DISC-04).
    verified = result.status in (FetchStatus.OK, FetchStatus.EMPTY)

    if result.status is FetchStatus.ERROR:
        kind = result.error_kind.value if result.error_kind else "unknown_error"
        reason = f"{kind}: http {result.http_status} — {result.message}"
    else:
        reason = ""

    return ProbeResult(
        platform=platform,
        slug=slug,
        verified=verified,
        job_count=len(result.listings),
        reason=reason,
    )
