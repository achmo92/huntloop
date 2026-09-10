"""Three-tier employer resolution orchestration (REG-01, REG-02, REG-03).

Resolution order (always run in this sequence — rationale in the docstring):

    Tier 2 (static sweep) → Tier 1 (name guesses, unconditional) →
    verify all candidates → Tier 3 (rendered sweep, only on failure)

``persist_resolution`` writes the result to the database via
``CompanyRepository.upsert_by_name``, which is the ONLY safe write path for
companies (see COMPANY_DISCOVERY_OWNED_COLUMNS in repository.py).

The full candidate and probe trail is persisted, not just the winner.  When a
slug silently stops working months later, the difference between "we guessed
and got lucky" and "the page told us" is the only thing that makes the failure
diagnosable — and REG-04's Phase 4 review surface reads exactly this block.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

from huntloop.db.models import AtsPlatform
from huntloop.discovery.ats.registry import ADAPTERS
from huntloop.discovery.fetch.page import PageFetcher, RendererUnavailable, StaticPageFetcher
from huntloop.registry.probe import ProbeResult, guess_slugs, probe_slug
from huntloop.registry.signatures import SlugCandidate, sweep_signatures

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Ordered preference for source evidence strength.
_SOURCE_ORDER = {"static_html": 0, "rendered_html": 1, "name_guess": 2}
_MAX_PROBES = 12


class ResolutionStatus(str, enum.Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    NEEDS_REVERIFICATION = "needs_reverification"


@dataclass(frozen=True)
class ResolutionResult:
    status: ResolutionStatus
    platform: str | None = None
    slug: str | None = None
    resolved_via: str | None = None  # static_html | rendered_html | name_guess
    candidates: tuple[SlugCandidate, ...] = ()
    probed: tuple[ProbeResult, ...] = ()
    careers_url: str | None = None
    final_url: str | None = None
    reason: str = ""


# ---------------------------------------------------------------------------
# Input normalisation helpers
# ---------------------------------------------------------------------------


def _normalise_url(url: str) -> str:
    """Prepend https:// to a bare domain."""
    if "://" not in url:
        return f"https://{url}"
    return url


def _fetch_with_fallback(
    fetcher: StaticPageFetcher,
    url: str,
) -> tuple[str, str | None]:
    """Fetch URL; if path-less, try /careers first, then root.

    Returns ``(html, final_url)``.
    """
    from urllib.parse import urlparse, urlunparse  # noqa: PLC0415

    parsed = urlparse(url)
    path = parsed.path.rstrip("/")

    if not path or path == "":
        # Try /careers first
        careers_url = urlunparse(parsed._replace(path="/careers"))
        result = fetcher.fetch(careers_url)
        if result.ok:
            return result.html, result.final_url

        # Fall back to root
        root_url = urlunparse(parsed._replace(path="/"))
        result = fetcher.fetch(root_url)
        return result.html if result.ok else "", result.final_url if result.ok else None

    result = fetcher.fetch(url)
    return result.html if result.ok else "", result.final_url if result.ok else None


# ---------------------------------------------------------------------------
# Probe deduplication and capping
# ---------------------------------------------------------------------------


def _probe_candidates(
    candidates: list[SlugCandidate],
    client: httpx.Client,
) -> list[ProbeResult]:
    """Probe candidates deduped by (platform, slug), strong sources first.

    Stops probing a platform once one slug is verified.  Caps total probes at
    ``_MAX_PROBES`` to bound per-employer work.
    """
    seen: set[tuple[str, str]] = set()
    ordered = sorted(
        [c for c in candidates if c.slug is not None],
        key=lambda c: (_SOURCE_ORDER.get(c.source, 99), c.slug),
    )

    probed: list[ProbeResult] = []
    verified_platforms: set[str] = set()

    for candidate in ordered:
        if len(probed) >= _MAX_PROBES:
            break
        key = (candidate.platform, candidate.slug)
        if key in seen:
            continue
        seen.add(key)

        if candidate.platform in verified_platforms:
            continue

        result = probe_slug(candidate.platform, candidate.slug, client=client)
        probed.append(result)

        if result.verified:
            verified_platforms.add(candidate.platform)

    return probed


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def resolve_employer(
    *,
    name: str,
    careers_url: str | None = None,
    client: httpx.Client,
    static_fetcher: PageFetcher | None = None,
    rendered_fetcher: PageFetcher | None = None,
    max_guesses: int = 5,
) -> ResolutionResult:
    """Resolve an employer's ATS platform and board slug using a three-tier strategy.

    Resolution order — rationale:

    1. **Normalise input (REG-01).**  Accept URL, bare domain, or name alone.
    2. **Tier 2 — static sweep.**  Fetch the careers page; sweep for ATS signatures.
       Fast and free; catches the majority of cases.
    3. **Tier 1 — name guesses, ALWAYS.**  Probe guessed slugs on all three ATS APIs.
       # Spotify: careers page has zero lever.co references anywhere, yet slug
       # "spotify" probes 200. Tier 1 cannot be conditional on Tier 2 failing.
    4. **Verify.**  All candidates probed; stop per-platform on first success.
    5. **Tier 3 — rendered sweep, conditional.**  Only when Tiers 1–2 found nothing.
    6. **Decide.**  0 verified → UNRESOLVED; 1 → RESOLVED; >1 → AMBIGUOUS.
    """
    if static_fetcher is None:
        static_fetcher = StaticPageFetcher()

    all_candidates: list[SlugCandidate] = []
    final_url: str | None = None
    tiers_ran: list[str] = []

    normalised_url: str | None = None
    if careers_url is not None:
        normalised_url = _normalise_url(careers_url)

    # ----- Tier 2: static HTML sweep -----------------------------------------
    if normalised_url is not None:
        tiers_ran.append("tier2_static")
        html, final_url = _fetch_with_fallback(
            static_fetcher,  # type: ignore[arg-type]
            normalised_url,
        )
        all_candidates.extend(sweep_signatures(html, source="static_html"))

    # ----- Tier 1: name guesses (unconditional) ------------------------------
    # Spotify: careers page has zero lever.co references anywhere, yet slug
    # "spotify" probes 200. Tier 1 cannot be conditional on Tier 2 failing.
    tiers_ran.append("tier1_guess")
    for slug in guess_slugs(name)[:max_guesses]:
        for platform in ADAPTERS:
            all_candidates.append(
                SlugCandidate(platform=platform, slug=slug, source="name_guess")
            )

    # ----- Verify: probe all unique (platform, slug) pairs -------------------
    probe_results = _probe_candidates(all_candidates, client)
    verified = [p for p in probe_results if p.verified]

    # ----- Tier 3: rendered sweep (only when Tiers 1+2 failed) --------------
    rendered_html: str | None = None
    tier3_reason: str = ""
    if not verified and normalised_url is not None and rendered_fetcher is not None:
        tiers_ran.append("tier3_rendered")
        try:
            page = rendered_fetcher.fetch(normalised_url)
            rendered_html = page.html if page.ok else ""
        except RendererUnavailable as exc:
            tier3_reason = f"rendered fetch unavailable: {exc}"
            rendered_html = ""

        if rendered_html:
            rendered_candidates = list(sweep_signatures(rendered_html, source="rendered_html"))
            if rendered_candidates:
                all_candidates.extend(rendered_candidates)
                extra_probes = _probe_candidates(rendered_candidates, client)
                probe_results = probe_results + extra_probes
                verified = [p for p in probe_results if p.verified]

    # ----- Decide ------------------------------------------------------------
    verified_pairs: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for p in probe_results:
        if p.verified:
            pair = (p.platform, p.slug)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                verified_pairs.append(pair)

    if len(verified_pairs) == 0:
        probe_count = len(probe_results)
        failed = [p for p in probe_results if not p.verified]
        reason_parts = [f"tiers ran: {', '.join(tiers_ran)}; {probe_count} probes, all failed"]
        if tier3_reason:
            reason_parts.append(tier3_reason)
        if failed:
            platforms_tried = sorted({p.platform for p in failed})
            reason_parts.append(f"platforms tried: {platforms_tried}")
        return ResolutionResult(
            status=ResolutionStatus.UNRESOLVED,
            candidates=tuple(all_candidates),
            probed=tuple(probe_results),
            careers_url=normalised_url,
            final_url=final_url,
            reason="; ".join(reason_parts),
        )

    if len(verified_pairs) > 1:
        return ResolutionResult(
            status=ResolutionStatus.AMBIGUOUS,
            slug=None,
            candidates=tuple(all_candidates),
            probed=tuple(probe_results),
            careers_url=normalised_url,
            final_url=final_url,
            reason=f"multiple verified candidates: {verified_pairs}",
        )

    # Exactly one winner.
    platform, slug = verified_pairs[0]
    # Determine resolved_via from the strongest-evidence candidate for this pair.
    resolved_via = _best_source(all_candidates, platform, slug)

    return ResolutionResult(
        status=ResolutionStatus.RESOLVED,
        platform=platform,
        slug=slug,
        resolved_via=resolved_via,
        candidates=tuple(all_candidates),
        probed=tuple(probe_results),
        careers_url=normalised_url,
        final_url=final_url,
    )


def _best_source(candidates: list[SlugCandidate], platform: str, slug: str) -> str:
    """Return the source of the strongest-evidence candidate for (platform, slug)."""
    matching = [
        c for c in candidates if c.platform == platform and c.slug == slug and not c.weak
    ]
    if not matching:
        return "name_guess"
    best = min(matching, key=lambda c: _SOURCE_ORDER.get(c.source, 99))
    return best.source


# ---------------------------------------------------------------------------
# ats_config builder
# ---------------------------------------------------------------------------


def build_ats_config(result: ResolutionResult) -> dict:
    """Build the JSON blob persisted in ``companies.ats_config``.

    The full candidate and probe trail is persisted, not just the winner.  When
    a slug silently stops working months later, the difference between "we guessed
    and got lucky" and "the page told us" is the only thing that makes the failure
    diagnosable — and REG-04's Phase 4 review surface reads exactly this block.

    ``checked_at`` is serialised as a string (not datetime) so ``json.dumps`` on
    the JSON column never fails.
    """
    return {
        "resolution": {
            "status": result.status.value,
            "resolved_via": result.resolved_via,
            "careers_url": result.careers_url,
            "final_url": result.final_url,
            "candidates": [
                {
                    "platform": c.platform,
                    "slug": c.slug,
                    "source": c.source,
                    "weak": c.weak,
                    "matched_patterns": list(c.matched_patterns),
                }
                for c in result.candidates
            ],
            "probed": [
                {
                    "platform": p.platform,
                    "slug": p.slug,
                    "verified": p.verified,
                    "job_count": p.job_count,
                    "reason": p.reason,
                }
                for p in result.probed
            ],
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "reason": result.reason,
        }
    }


# ---------------------------------------------------------------------------
# Persistence helper
# ---------------------------------------------------------------------------


def persist_resolution(
    session: "Session",
    name: str,
    result: ResolutionResult,
    **extra,
) -> "object":
    """Persist a resolution result via CompanyRepository.upsert_by_name.

    Maps ``result.platform`` to the actual ``AtsPlatform`` enum member.  If
    ``result.status`` is ``RESOLVED``, ``resolved_at`` is set to now; otherwise
    it is left as None so incomplete resolution is queryable.
    """
    from huntloop.db.repository import CompanyRepository  # noqa: PLC0415

    # Map the string platform name to the AtsPlatform enum member.
    ats_value: AtsPlatform | None = None
    if result.platform is not None:
        try:
            ats_value = AtsPlatform(result.platform)
        except ValueError:
            ats_value = None

    resolved_at = (
        datetime.now(timezone.utc) if result.status is ResolutionStatus.RESOLVED else None
    )

    repo = CompanyRepository(session)
    repo.upsert_by_name(
        name,
        ats=ats_value,
        ats_identifier=result.slug,
        ats_config=build_ats_config(result),
        careers_url=result.careers_url,
        resolved_at=resolved_at,
        **extra,
    )
    return repo.get_by_name(name)
