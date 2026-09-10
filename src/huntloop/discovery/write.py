"""The single write path from ScoredListing to the jobs table (DISC-06, TRAK-06).

Every fetched listing is persisted with its filter_tier_reached stamped,
including deterministic-filter rejections. A listing dropped by the filters
is written with no score. This ensures filter aggressiveness remains auditable,
dedup works across runs for filtered listings, and loosening criteria does not
require a re-fetch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from huntloop.db.repository import JobRepository
from huntloop.discovery.dedup import compute_dedup_key, DedupKeyError

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class WriteOutcome:
    inserted: int = 0
    updated: int = 0
    failed: int = 0
    errors: tuple[str, ...] = ()


def write_listing(session, company, scored, *, run_id, now=None) -> str:
    """Insert or update a job row for a ScoredListing.
    
    Returns "inserted" if it was a new listing, or "updated" if it already existed.
    """
    key = compute_dedup_key(company.name, scored.listing)
    repo = JobRepository(session)
    existing = repo.get_by_dedup_key(key)
    
    now = now or datetime.now(timezone.utc)
    
    # Written unconditionally, before any check on scored.scored. A filtered-out
    # listing carries no score but still gets a row with its filter_tier_reached stamped:
    # dedup works across runs for filtered listings too, filter aggressiveness stays
    # auditable, and loosening criteria later does not force a re-fetch. Suppressing
    # them would make the pipeline's most consequential decisions invisible.
    fields = {
        "dedup_key": key,
        "company_id": company.id,
        "external_id": scored.listing.external_id,
        "url": scored.listing.url,
        "title": scored.listing.title,
        "location_raw": scored.listing.location_raw,
        "location_normalized": getattr(scored.listing, "location_normalized", None),
        "is_remote": scored.listing.is_remote,
        "remote_scope": getattr(scored.listing, "remote_scope", None),
        "location_eligible": getattr(scored.listing, "location_eligible", None),
        "posted_at": scored.listing.posted_at,
        "description": scored.listing.description_html or scored.listing.description_plain,
        "comp_raw": scored.listing.comp_raw,
        "comp_min": scored.listing.comp_min,
        "comp_max": scored.listing.comp_max,
        "comp_currency": scored.listing.comp_currency,
        "comp_period": scored.listing.comp_period,
        "filter_tier_reached": scored.tier_reached,
        "source_run_id": run_id,
        "last_seen_at": now,
    }
    
    # We do NOT include work_auth_required as it's not present on RawListing
    # but could be added later. It's in the JOB_DISCOVERY_OWNED_COLUMNS but optional here.
    
    if existing is None:
        fields["first_seen_at"] = now
        
    repo.upsert_discovered(**fields)
    
    # Upsert does not return the object, we have to fetch to get ID to mark scored
    job = repo.get_by_dedup_key(key)
    
    if scored.scored:
        repo.mark_scored(
            job.id,
            score_overall=scored.overall,
            score_dimensions=scored.dimensions,
            score_flags=scored.flags,
            score_summary=scored.summary,
            criteria_version=scored.criteria_version,
            rubric_version=scored.rubric_version,
            model=scored.model,
            filter_tier=scored.tier_reached,
        )
        
    return "updated" if existing else "inserted"


def write_batch(session, company, scored_listings, *, run_id, now=None) -> WriteOutcome:
    """Write a batch of ScoredListing objects, isolating errors per listing."""
    inserted = 0
    updated = 0
    failed = 0
    errors = []
    
    for scored in scored_listings:
        # Per-listing isolation. DISC-03's fault tolerance applies at the employer
        # level, but one malformed listing must not cost an employer its other 40.
        try:
            res = write_listing(session, company, scored, run_id=run_id, now=now)
            if res == "inserted":
                inserted += 1
            else:
                updated += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{scored.listing.title}: {exc}")
            
    session.flush()
    return WriteOutcome(inserted=inserted, updated=updated, failed=failed, errors=tuple(errors))
