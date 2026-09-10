"""Tests for huntloop.scoring.flags (SCOR-09)."""

import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from huntloop.criteria.schema import (
    SENIORITY_LADDER,
    CompensationFloor,
    CriteriaPayload,
    DimensionWeights,
    LocationCriteria,
)
from huntloop.discovery.normalize.compensation import FloorComparison, NormalizedCompensation
from huntloop.discovery.normalize.location import NormalizedLocation, RemoteScopeGuess
from huntloop.scoring.filters import FilterDecision, FilterVerdict
from huntloop.scoring.flags import (
    FLAG_NAMES,
    SENIORITY_MARKERS,
    compute_flags,
    detect_seniority,
)

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@dataclass
class _Listing:
    title: str = "Software Engineer"
    description_plain: str = ""
    description_html: str = ""
    posted_at: datetime | None = None
    location_raw: str | None = None
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
    is_remote: bool | None = None,
    remote_scope: RemoteScopeGuess = RemoteScopeGuess.UNSPECIFIED,
    ambiguous: bool = False,
) -> NormalizedLocation:
    return NormalizedLocation(
        raw=raw,
        country_code=country_code,
        region=region,
        city=None,
        is_remote=is_remote,
        remote_scope=remote_scope,
        ambiguous=ambiguous,
        display="",
    )


def _comp(
    minimum=None,
    maximum=None,
    currency=None,
    period=None,
    raw=None,
    source="absent",
) -> NormalizedCompensation:
    return NormalizedCompensation(
        minimum=minimum,
        maximum=maximum,
        currency=currency,
        period=period,
        raw=raw,
        source=source,
    )


def _pass_decision(detail: str = "ok", uncertain: bool = False) -> FilterDecision:
    return FilterDecision(
        rule="posting_age",
        verdict=FilterVerdict.PASS,
        detail=detail,
        uncertain=uncertain,
    )


def _minimal_flags(
    *,
    title: str = "Software Engineer",
    description: str = "",
    posted_at: datetime | None = None,
    ambiguous: bool = False,
    floor_comparison: FloorComparison = FloorComparison.NO_DATA,
    comp: NormalizedCompensation | None = None,
    criteria: CriteriaPayload | None = None,
    posting_age_decision: FilterDecision | None = None,
) -> dict:
    listing = _Listing(title=title, description_plain=description, posted_at=posted_at)
    location = _loc(ambiguous=ambiguous)
    comp = comp or _comp()
    criteria = criteria or _minimal_criteria()
    posting_age_decision = posting_age_decision or _pass_decision()
    return compute_flags(
        listing=listing,
        location=location,
        comp=comp,
        floor_comparison=floor_comparison,
        criteria=criteria,
        posting_age_decision=posting_age_decision,
    )


# ===========================================================================
# FLAG_NAMES contract
# ===========================================================================


class TestFlagNames:
    def test_flag_names_exact_tuple(self):
        assert FLAG_NAMES == (
            "stretch_role",
            "step_down",
            "language_requirement",
            "location_ambiguity",
            "comp_below_floor",
            "posting_stale",
        )

    def test_all_six_keys_always_present(self):
        result = _minimal_flags()
        assert set(result.keys()) == set(FLAG_NAMES)

    def test_compute_flags_has_no_score_parameter(self):
        """Flags are advisory — the signature must carry no score argument."""
        sig = inspect.signature(compute_flags)
        assert "score" not in sig.parameters

    def test_no_drop_or_reject_key_in_output(self):
        result = _minimal_flags()
        for key in result:
            assert "drop" not in key
            assert "reject" not in key
            assert "exclude" not in key


# ===========================================================================
# Seniority markers alignment
# ===========================================================================


class TestSeniorityMarkers:
    def test_all_marker_values_are_ladder_members(self):
        for _pattern, rung in SENIORITY_MARKERS:
            assert rung in SENIORITY_LADDER, f"{rung!r} not in SENIORITY_LADDER"

    def test_detect_seniority_senior(self):
        assert detect_seniority("Senior Software Engineer") == "senior"

    def test_detect_seniority_staff_over_senior(self):
        # "Senior Staff Engineer" should resolve to staff (highest wins first).
        result = detect_seniority("Senior Staff Engineer")
        assert result == "staff"

    def test_detect_seniority_no_match(self):
        assert detect_seniority("Software Engineer") is None

    def test_detect_seniority_intern(self):
        assert detect_seniority("Summer Internship - Engineering") == "intern"

    def test_detect_seniority_c_level(self):
        assert detect_seniority("Chief Technology Officer") == "c_level"

    def test_detect_seniority_vp(self):
        assert detect_seniority("VP of Engineering") == "vp"


# ===========================================================================
# stretch_role / step_down flags
# ===========================================================================


class TestStretchAndStepDown:
    def test_stretch_role_raised_when_above_max(self):
        criteria = _minimal_criteria(seniority_max="senior")
        result = _minimal_flags(title="Principal Engineer", criteria=criteria)
        assert result["stretch_role"]["raised"] is True
        assert result["step_down"]["raised"] is False

    def test_step_down_raised_when_below_min(self):
        criteria = _minimal_criteria(seniority_min="senior")
        result = _minimal_flags(title="Junior Software Engineer", criteria=criteria)
        assert result["step_down"]["raised"] is True
        assert result["stretch_role"]["raised"] is False

    def test_neither_raised_within_bounds(self):
        criteria = _minimal_criteria(seniority_min="junior", seniority_max="senior")
        result = _minimal_flags(title="Software Engineer", criteria=criteria)
        # A title without a level marker doesn't raise either.
        assert result["stretch_role"]["raised"] is False
        assert result["step_down"]["raised"] is False

    def test_stretch_and_step_down_never_both_raised(self):
        """Structural exclusivity check — they use elif, can never coexist."""
        criteria = _minimal_criteria(seniority_min="junior", seniority_max="senior")
        for title in [
            "Intern Engineer",
            "Junior Software Engineer",
            "Senior Software Engineer",
            "Principal Engineer",
            "VP Engineering",
        ]:
            result = _minimal_flags(title=title, criteria=criteria)
            assert not (
                result["stretch_role"]["raised"] and result["step_down"]["raised"]
            ), f"Both raised for title: {title!r}"

    def test_no_max_raises_no_stretch(self):
        # An unset seniority_max is not a bound.
        criteria = _minimal_criteria(seniority_max=None)
        result = _minimal_flags(title="CTO", criteria=criteria)
        assert result["stretch_role"]["raised"] is False

    def test_no_min_raises_no_step_down(self):
        criteria = _minimal_criteria(seniority_min=None)
        result = _minimal_flags(title="Intern", criteria=criteria)
        assert result["step_down"]["raised"] is False

    def test_unrecognised_title_raises_neither(self):
        criteria = _minimal_criteria(seniority_min="junior", seniority_max="senior")
        result = _minimal_flags(title="Barista", criteria=criteria)
        assert result["stretch_role"]["raised"] is False
        assert result["step_down"]["raised"] is False


# ===========================================================================
# language_requirement
# ===========================================================================


class TestLanguageRequirement:
    def test_german_required_raises_flag(self):
        result = _minimal_flags(description="You must be fluent German required for this role.")
        assert result["language_requirement"]["raised"] is True
        assert "German" in result["language_requirement"]["detail"]

    def test_english_only_does_not_raise(self):
        result = _minimal_flags(description="Fluent English required.")
        assert result["language_requirement"]["raised"] is False

    def test_no_language_requirement_does_not_raise(self):
        result = _minimal_flags(description="Great communication skills needed.")
        assert result["language_requirement"]["raised"] is False


# ===========================================================================
# location_ambiguity
# ===========================================================================


class TestLocationAmbiguity:
    def test_ambiguous_location_raises_flag(self):
        listing = _Listing()
        location = _loc(ambiguous=True, raw="Zzzz Qqqq")
        comp = _comp()
        criteria = _minimal_criteria()
        result = compute_flags(
            listing=listing,
            location=location,
            comp=comp,
            floor_comparison=FloorComparison.NO_DATA,
            criteria=criteria,
            posting_age_decision=_pass_decision(),
        )
        assert result["location_ambiguity"]["raised"] is True

    def test_non_ambiguous_does_not_raise(self):
        result = _minimal_flags(ambiguous=False)
        assert result["location_ambiguity"]["raised"] is False


# ===========================================================================
# comp_below_floor
# ===========================================================================


class TestCompBelowFloor:
    def test_below_raises(self):
        result = _minimal_flags(floor_comparison=FloorComparison.BELOW)
        assert result["comp_below_floor"]["raised"] is True

    def test_not_comparable_does_not_raise(self):
        """An INR salary numerically larger than a USD floor must NOT be flagged."""
        result = _minimal_flags(floor_comparison=FloorComparison.NOT_COMPARABLE)
        assert result["comp_below_floor"]["raised"] is False

    def test_no_data_does_not_raise(self):
        result = _minimal_flags(floor_comparison=FloorComparison.NO_DATA)
        assert result["comp_below_floor"]["raised"] is False

    def test_above_does_not_raise(self):
        result = _minimal_flags(floor_comparison=FloorComparison.ABOVE)
        assert result["comp_below_floor"]["raised"] is False


# ===========================================================================
# posting_stale
# ===========================================================================


class TestPostingStale:
    def test_no_date_decision_raises_stale(self):
        # A "no reliable date" uncertain decision triggers posting_stale.
        no_date_decision = _pass_decision(
            detail="posting has no reliable date; kept as candidate",
            uncertain=True,
        )
        listing = _Listing()
        result = compute_flags(
            listing=listing,
            location=_loc(),
            comp=_comp(),
            floor_comparison=FloorComparison.NO_DATA,
            criteria=_minimal_criteria(),
            posting_age_decision=no_date_decision,
        )
        assert result["posting_stale"]["raised"] is True
        assert "no date" in result["posting_stale"]["detail"]

    def test_old_posting_raises_stale(self):
        # A posting older than posting_age_days should raise posting_stale.
        old_date = NOW - timedelta(days=60)
        listing = _Listing(posted_at=old_date)
        criteria = _minimal_criteria(posting_age_days=30)
        result = compute_flags(
            listing=listing,
            location=_loc(),
            comp=_comp(),
            floor_comparison=FloorComparison.NO_DATA,
            criteria=criteria,
            posting_age_decision=_pass_decision(),
        )
        assert result["posting_stale"]["raised"] is True

    def test_fresh_posting_does_not_raise(self):
        fresh = datetime.now(timezone.utc) - timedelta(days=5)
        listing = _Listing(posted_at=fresh)
        criteria = _minimal_criteria(posting_age_days=30)
        result = compute_flags(
            listing=listing,
            location=_loc(),
            comp=_comp(),
            floor_comparison=FloorComparison.NO_DATA,
            criteria=criteria,
            posting_age_decision=_pass_decision(),
        )
        assert result["posting_stale"]["raised"] is False


# ===========================================================================
# Module-level model isolation
# ===========================================================================


class TestModuleIsolation:
    def test_no_model_imports_in_flags(self):
        import ast
        import pathlib

        src = pathlib.Path("src/huntloop/scoring/flags.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [getattr(a, "name", "") or "" for a in getattr(node, "names", [])]
                module = getattr(node, "module", "") or ""
                assert "openai" not in module and "openai" not in " ".join(names)
                assert "huntloop.llm" not in module
