"""Tests for huntloop.scoring.filters (SCOR-01, SCOR-03).

Run SCOR-01 tests:  pytest tests/scoring/test_filters.py -k posting_age -q
Run SCOR-03 tests:  pytest tests/scoring/test_filters.py -k geography -q
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Literal

import pytest

from huntloop.criteria.schema import CriteriaPayload, DimensionWeights, LocationCriteria
from huntloop.discovery.normalize.location import (
    NormalizedLocation,
    RemoteScopeGuess,
)
from huntloop.scoring.filters import (
    FilterDecision,
    FilterOutcome,
    FilterVerdict,
    apply_deterministic_filters,
    check_geography,
    check_posting_age,
)

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Minimal stub for RawListing (avoids importing the full ATS base at test time)
# ---------------------------------------------------------------------------


@dataclass
class _Listing:
    location_raw: str | None = None
    country_code: str | None = None
    is_remote: bool | None = None
    workplace_type: str | None = None
    secondary_locations: list[str] | None = None
    posted_at: datetime | None = None
    title: str = "Software Engineer"
    description_plain: str = ""
    description_html: str = ""
    comp_min: object = None
    comp_max: object = None
    comp_currency: str | None = None
    comp_period: str | None = None
    comp_raw: str | None = None


def _minimal_criteria(**kwargs) -> CriteriaPayload:
    defaults = dict(
        profile_summary="SWE",
        dimension_weights=DimensionWeights(
            role_fit=0.25, seniority_fit=0.25, employer_fit=0.25, trajectory=0.25
        ),
    )
    defaults.update(kwargs)
    return CriteriaPayload(**defaults)


def _loc(
    raw: str | None = None,
    country_code: str | None = None,
    region: str | None = None,
    city: str | None = None,
    is_remote: bool | None = None,
    remote_scope: RemoteScopeGuess = RemoteScopeGuess.UNSPECIFIED,
    ambiguous: bool = False,
    display: str = "",
) -> NormalizedLocation:
    return NormalizedLocation(
        raw=raw,
        country_code=country_code,
        region=region,
        city=city,
        is_remote=is_remote,
        remote_scope=remote_scope,
        ambiguous=ambiguous,
        display=display,
    )


# ===========================================================================
# SCOR-01: Posting-age filter
# ===========================================================================


class Test_posting_age_Filter:
    """Tests for check_posting_age (SCOR-01)."""

    def test_within_limit_passes(self):
        d = check_posting_age(NOW - timedelta(days=10), 30, now=NOW)
        assert d.verdict is FilterVerdict.PASS
        assert d.uncertain is False
        assert "10d" in d.detail

    def test_older_than_limit_drops(self):
        d = check_posting_age(NOW - timedelta(days=45), 30, now=NOW)
        assert d.verdict is FilterVerdict.DROP
        assert d.rule == "posting_age"
        assert "45d" in d.detail
        assert "30d" in d.detail

    def test_none_posted_at_passes_uncertain(self):
        d = check_posting_age(None, 30, now=NOW)
        assert d.verdict is FilterVerdict.PASS
        assert d.uncertain is True
        assert "no reliable date" in d.detail
        assert "kept as candidate" in d.detail

    def test_none_max_age_passes_old_posting(self):
        very_old = NOW - timedelta(days=365 * 5)
        d = check_posting_age(very_old, None, now=NOW)
        assert d.verdict is FilterVerdict.PASS

    def test_boundary_equal_to_limit_passes(self):
        # Exactly max_age_days old → PASS (inclusive)
        d = check_posting_age(NOW - timedelta(days=30), 30, now=NOW)
        assert d.verdict is FilterVerdict.PASS

    def test_one_over_limit_drops(self):
        d = check_posting_age(NOW - timedelta(days=31), 30, now=NOW)
        assert d.verdict is FilterVerdict.DROP

    def test_future_date_passes_uncertain(self):
        d = check_posting_age(NOW + timedelta(days=2), 30, now=NOW)
        assert d.verdict is FilterVerdict.PASS
        assert d.uncertain is True
        assert "future" in d.detail

    def test_naive_datetime_treated_as_utc(self):
        # A naive datetime 10 days ago should still pass (treated as UTC).
        naive = (NOW - timedelta(days=10)).replace(tzinfo=None)
        d = check_posting_age(naive, 30, now=NOW)
        assert d.verdict is FilterVerdict.PASS


# ===========================================================================
# SCOR-03: Geography eligibility filter
# ===========================================================================


class Test_geography_Filter:
    """Tests for check_geography (SCOR-03)."""

    def test_country_in_eligible_list_passes(self):
        location = _loc(country_code="IN")
        criteria = LocationCriteria(eligible_countries=["IN", "US"])
        d = check_geography(location, criteria)
        assert d.verdict is FilterVerdict.PASS
        assert "IN" in d.detail

    def test_country_not_in_list_drops(self):
        location = _loc(country_code="DE")
        criteria = LocationCriteria(eligible_countries=["IN", "US"], eligible_regions=[])
        d = check_geography(location, criteria)
        assert d.verdict is FilterVerdict.DROP
        assert d.rule == "geography"
        assert "DE" in d.detail

    def test_global_remote_passes_regardless_of_country(self):
        location = _loc(country_code="DE", remote_scope=RemoteScopeGuess.GLOBAL)
        criteria = LocationCriteria(eligible_countries=["US"])
        d = check_geography(location, criteria)
        assert d.verdict is FilterVerdict.PASS

    def test_remote_country_ineligible_drops(self):
        # Remote WITHIN a country the user cannot work in → DROP (SCOR-03 literal case).
        location = _loc(country_code="DE", remote_scope=RemoteScopeGuess.COUNTRY)
        criteria = LocationCriteria(eligible_countries=["US"])
        d = check_geography(location, criteria)
        assert d.verdict is FilterVerdict.DROP

    def test_region_match_passes(self):
        location = _loc(region="EMEA", remote_scope=RemoteScopeGuess.REGION)
        criteria = LocationCriteria(eligible_regions=["EMEA"])
        d = check_geography(location, criteria)
        assert d.verdict is FilterVerdict.PASS

    def test_region_miss_without_country_drops(self):
        location = _loc(region="APAC", country_code=None, remote_scope=RemoteScopeGuess.REGION)
        criteria = LocationCriteria(eligible_regions=["EMEA"])
        d = check_geography(location, criteria)
        assert d.verdict is FilterVerdict.DROP

    def test_region_rescues_unlisted_country(self):
        # Country not individually listed, but region is eligible → PASS.
        location = _loc(country_code="DE", region="EMEA")
        criteria = LocationCriteria(eligible_countries=["US"], eligible_regions=["EMEA"])
        d = check_geography(location, criteria)
        assert d.verdict is FilterVerdict.PASS

    def test_empty_lists_pass_any_country(self):
        location = _loc(country_code="DE")
        criteria = LocationCriteria(eligible_countries=[], eligible_regions=[])
        d = check_geography(location, criteria)
        assert d.verdict is FilterVerdict.PASS
        assert "no geographic eligibility configured" in d.detail

    def test_ambiguous_location_passes_uncertain(self):
        location = _loc(ambiguous=True, country_code=None, region=None)
        criteria = LocationCriteria(eligible_countries=["US"])
        d = check_geography(location, criteria)
        assert d.verdict is FilterVerdict.PASS
        assert d.uncertain is True
        # Explicitly NOT a drop
        assert d.verdict is not FilterVerdict.DROP

    def test_unspecified_remote_scope_passes_uncertain(self):
        location = _loc(is_remote=True, remote_scope=RemoteScopeGuess.UNSPECIFIED, country_code=None)
        criteria = LocationCriteria(eligible_countries=["US"])
        d = check_geography(location, criteria)
        assert d.verdict is FilterVerdict.PASS
        assert d.uncertain is True

    def test_preferred_cities_never_drop(self):
        """check_geography must NOT read location.city or criteria.preferred_cities."""
        location = _loc(city="Berlin", country_code=None, region=None)
        criteria = LocationCriteria(
            eligible_countries=["US"],
            preferred_cities=["New York"],
        )
        # Should fall to step 6 (uncertain pass) since country/region can't be resolved.
        d = check_geography(location, criteria)
        # Must not DROP just because preferred city doesn't match.
        assert d.verdict is FilterVerdict.PASS


# ===========================================================================
# apply_deterministic_filters
# ===========================================================================


class TestApplyDeterministicFilters:
    def test_passing_listing_returns_passed_true(self):
        listing = _Listing(
            location_raw="San Francisco, US",
            country_code="US",
            posted_at=NOW - timedelta(days=5),
        )
        criteria = _minimal_criteria(
            posting_age_days=30,
            locations=LocationCriteria(eligible_countries=["US"]),
        )
        outcome = apply_deterministic_filters(listing, criteria, now=NOW)
        assert outcome.passed is True
        assert outcome.tier_reached.value == "deterministic"

    def test_dropped_listing_has_first_drop(self):
        listing = _Listing(
            location_raw="San Francisco, US",
            country_code="US",
            posted_at=NOW - timedelta(days=100),
        )
        criteria = _minimal_criteria(posting_age_days=30)
        outcome = apply_deterministic_filters(listing, criteria, now=NOW)
        assert outcome.passed is False
        assert outcome.first_drop is not None
        assert outcome.first_drop.rule == "posting_age"

    def test_short_circuits_after_first_drop(self):
        listing = _Listing(
            location_raw="Germany",
            country_code="DE",
            posted_at=NOW - timedelta(days=100),  # will drop on age
        )
        criteria = _minimal_criteria(
            posting_age_days=30,
            locations=LocationCriteria(eligible_countries=["US"]),
        )
        outcome = apply_deterministic_filters(listing, criteria, now=NOW)
        assert outcome.passed is False
        # geography should be SKIPPED, not DROP
        geo = next(d for d in outcome.decisions if d.rule == "geography")
        assert geo.verdict is FilterVerdict.SKIPPED

    def test_location_is_exposed_on_outcome(self):
        listing = _Listing(location_raw="Remote, US", is_remote=True, country_code="US")
        criteria = _minimal_criteria()
        outcome = apply_deterministic_filters(listing, criteria, now=NOW)
        assert outcome.location is not None
        assert outcome.location.country_code == "US"

    def test_no_model_import_in_filters_module(self):
        """Grep-level: filters.py must not import huntloop.llm or openai."""
        import ast
        import pathlib

        src = pathlib.Path("src/huntloop/scoring/filters.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [
                    getattr(a, "name", "") or ""
                    for a in getattr(node, "names", [])
                ]
                module = getattr(node, "module", "") or ""
                assert "openai" not in module and "openai" not in " ".join(names), (
                    "filters.py must not import openai"
                )
                assert "huntloop.llm" not in module, (
                    "filters.py must not import huntloop.llm"
                )
