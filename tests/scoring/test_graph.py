"""Tests for triage pass and full scoring pipeline (SCOR-05).

Validates that triage runs on the cheap model, that scoring is skipped for triage drops,
and that the deterministic filters run before either model.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import httpx

from huntloop.criteria.schema import (
    CompensationFloor,
    CriteriaPayload,
    DimensionWeights,
    Exclusions,
    LocationCriteria,
    WorkAuthorization,
)
from huntloop.db.models import FilterTier
from huntloop.discovery.ats.base import RawListing
from huntloop.llm.client import LlmResponseError
from huntloop.scoring.config import active_scoring_config
from huntloop.scoring.pipeline import score_listing
from huntloop.scoring.triage import (
    TRIAGE_PROMPT,
    TriagePayload,
    build_triage_user_message,
    triage_listing,
)


class RecordingClient:
    """A fake OpenAI client that returns a sequence of responses and records arguments."""
    
    def __init__(self, responses: list[dict | Exception]) -> None:
        self.responses = responses
        self.call_idx = 0
        self.calls: list[dict] = []
        
    class Completions:
        def __init__(self, parent):
            self.parent = parent
            
        def create(self, **kwargs):
            self.parent.calls.append(kwargs)
            
            if self.parent.call_idx >= len(self.parent.responses):
                raise RuntimeError(f"Ran out of mocked responses. Calls so far: {self.parent.call_idx}")
                
            resp = self.parent.responses[self.parent.call_idx]
            self.parent.call_idx += 1
            
            if isinstance(resp, Exception):
                raise resp
                
            from collections import namedtuple
            Choice = namedtuple("Choice", ["message"])
            Message = namedtuple("Message", ["content"])
            Usage = namedtuple("Usage", ["prompt_tokens", "completion_tokens"])
            
            choice = Choice(message=Message(content=json.dumps(resp)))
            
            class FakeCompletion:
                choices = [choice]
                usage = Usage(prompt_tokens=10, completion_tokens=20)
                model = kwargs.get("model", "fake-model")
                
            return FakeCompletion()
            
    @property
    def chat(self):
        class Chat:
            completions = self.Completions(self)
        return Chat()

    @property
    def call_models(self) -> list[str]:
        return [c.get("model", "unknown") for c in self.calls]


@pytest.fixture
def default_criteria():
    return CriteriaPayload(
        profile_summary="Senior Python backend developer",
        seniority_min="senior",
        seniority_max="principal",
        dimension_weights=DimensionWeights(
            role_fit=0.4,
            seniority_fit=0.2,
            employer_fit=0.2,
            trajectory=0.2,
        ),
        locations=LocationCriteria(
            eligible_countries=["US"],
        ),
        compensation_floor=CompensationFloor(
            amount="120000",
            currency="USD",
            period="annual",
        ),
    )


@pytest.fixture
def default_listing():
    now = datetime.now(timezone.utc)
    return RawListing(
        external_id="ext-1",
        url="https://example.com/job/1",
        title="Senior Software Engineer",
        description_plain="We are looking for a Python engineer...",
        description_html="<p>We are looking for a Python engineer...</p>",
        location_raw="New York, NY",
        comp_raw="$130k - $150k",
        posted_at=now,
    )


class TestTriage:
    
    def test_triage_keep_true(self, default_listing, default_criteria):
        client = RecordingClient([{"keep": True, "reason": "Looks good"}])
        verdict = triage_listing(client, default_listing, default_criteria)
        
        assert verdict.keep is True
        assert verdict.reason == "Looks good"
        assert verdict.errored is False
        assert verdict.usage is not None
        
    def test_triage_uses_triage_model(self, default_listing, default_criteria):
        client = RecordingClient([{"keep": True, "reason": "Looks good"}])
        verdict = triage_listing(client, default_listing, default_criteria)
        
        from huntloop.config import load_config
        assert verdict.model == load_config().triage_model
        assert load_config().triage_model != load_config().scoring_model  # They must differ
        
    def test_triage_keep_false(self, default_listing, default_criteria):
        client = RecordingClient([{"keep": False, "reason": "Not a match"}])
        verdict = triage_listing(client, default_listing, default_criteria)
        
        assert verdict.keep is False
        assert verdict.reason == "Not a match"
        
    def test_system_message_is_constant(self, default_listing, default_criteria):
        client = RecordingClient([{"keep": True, "reason": "R"}])
        triage_listing(client, default_listing, default_criteria)
        
        messages = client.calls[0]["messages"]
        system_msg = next(m["content"] for m in messages if m["role"] == "system")
        assert system_msg == TRIAGE_PROMPT
        
    def test_user_message_truncates_description(self, default_criteria):
        from huntloop.scoring.triage import MAX_TRIAGE_DESCRIPTION_CHARS
        
        long_desc = "A" * (MAX_TRIAGE_DESCRIPTION_CHARS + 1000)
        listing = RawListing(
            external_id="1", url="1", title="Title", description_plain=long_desc
        )
        
        msg = build_triage_user_message(listing, default_criteria)
        
        # Make sure the full description isn't in there
        assert long_desc not in msg
        # But the truncated one is
        assert "A" * MAX_TRIAGE_DESCRIPTION_CHARS in msg
        
    def test_triage_exception_fails_open(self, default_listing, default_criteria):
        """Any unexpected exception yields keep=True."""
        client = RecordingClient([ValueError("Network error")])
        verdict = triage_listing(client, default_listing, default_criteria)
        
        assert verdict.keep is True
        assert verdict.errored is True
        assert "Network error" in verdict.reason
        
    def test_empty_reason_is_defaulted(self, default_listing, default_criteria):
        client = RecordingClient([{"keep": True, "reason": ""}])
        verdict = triage_listing(client, default_listing, default_criteria)
        
        assert verdict.keep is True
        assert verdict.reason == "no reason given"


class TestPipeline:
    
    def test_deterministic_drop_skips_models(self, default_criteria):
        """Filters run before models. A filter drop costs 0 model calls."""
        # This listing has a posting date that fails the posting_age deterministic filter
        criteria = default_criteria.model_copy()
        criteria.posting_age_days = 7
        
        from datetime import datetime, timezone, timedelta
        stale_date = datetime.now(timezone.utc) - timedelta(days=10)
        
        listing = RawListing(
            external_id="1", url="1", title="Engineering Manager", 
            description_plain="desc", location_raw="Remote",
            posted_at=stale_date
        )
        
        client = RecordingClient([])  # Will raise if called
        
        result = score_listing(client, listing, criteria, criteria_version=1)
        
        assert result.scored is False
        assert result.tier_reached == FilterTier.DETERMINISTIC
        assert result.drop_reason != ""
        assert len(client.calls) == 0
        
    def test_triage_drop_skips_scoring(self, default_listing, default_criteria):
        """Triage runs before scoring. A triage drop costs exactly 1 model call."""
        client = RecordingClient([
            {"keep": False, "reason": "Not a match"}  # Triage
        ])
        
        result = score_listing(client, default_listing, default_criteria, criteria_version=1)
        
        assert result.scored is False
        assert result.tier_reached == FilterTier.TRIAGE
        assert "triage: Not a match" in result.drop_reason
        assert len(client.calls) == 1
        
    def test_triage_before_score(self, default_listing, default_criteria):
        """SCOR-05: surviving listing records exactly two model calls in order."""
        client = RecordingClient([
            {"keep": True, "reason": "Looks good"},  # Triage
            {                                        # Scoring
                "role_fit": {"score": 5, "reason": "R"},
                "seniority_fit": {"score": None, "reason": "R"},
                "employer_fit": {"score": 3, "reason": "R"},
                "trajectory": {"score": 3, "reason": "R"},
                "summary": "Solid",
            }
        ])
        
        result = score_listing(client, default_listing, default_criteria, criteria_version=1)
        
        assert result.scored is True
        assert result.tier_reached == FilterTier.FULL
        assert len(client.calls) == 2
        
        from huntloop.config import load_config
        cfg = load_config()
        # Assert call sequence directly
        assert client.call_models[0] == cfg.triage_model
        assert client.call_models[1] == cfg.scoring_model
        
        # Renormalised over the three assessed dimensions: (0.4*5 + 0.2*3 + 0.2*3) / 0.8 = 4.00
        assert result.overall == Decimal("4.00")
        
        # Carries the three-axis stamp
        assert result.criteria_version == 1
        assert result.rubric_version == active_scoring_config().version
        assert result.model == cfg.scoring_model
        assert "role_fit" in result.dimensions
        
        # Has flags
        assert result.flags is not None
        assert "stretch_role" in result.flags
        
    def test_unscoreable_listing_handled_gracefully(self, default_listing, default_criteria):
        """All dimensions null yields scored=True, overall=None, plus a note."""
        client = RecordingClient([
            {"keep": True, "reason": "Looks good"},
            {
                "role_fit": {"score": None, "reason": "R"},
                "seniority_fit": {"score": None, "reason": "R"},
                "employer_fit": {"score": None, "reason": "R"},
                "trajectory": {"score": None, "reason": "R"},
                "summary": "Nothing known",
            }
        ])
        
        result = score_listing(client, default_listing, default_criteria, criteria_version=1)
        
        assert result.scored is True
        assert result.overall is None
        assert result.note != ""
        assert "overall not computable" in result.note
        
    def test_llm_error_during_scoring_handled(self, default_listing, default_criteria):
        """A failure in scoring yields scored=False, tier=TRIAGE, and does NOT propagate."""
        client = RecordingClient([
            {"keep": True, "reason": "Looks good"},
            import_openai_error()  # Helper to get the right error type
        ])
        
        result = score_listing(client, default_listing, default_criteria, criteria_version=1)
        
        assert result.scored is False
        assert result.tier_reached == FilterTier.TRIAGE
        assert "scoring failed" in result.error
        
        
def import_openai_error():
    from huntloop.llm.client import LlmResponseError
    return LlmResponseError("Bad schema")
