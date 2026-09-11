"""SCOR-06 tests: Four-dimension scoring with one-line reasons.

Tests run without network access or real model inference.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from huntloop.criteria.schema import (
    CompensationFloor,
    CriteriaPayload,
    DimensionWeights,
    Exclusions,
    LocationCriteria,
    WorkAuthorization,
)
from huntloop.discovery.ats.base import RawListing
from huntloop.llm.client import LlmCall, LlmResponseError, LlmUsage
from huntloop.scoring.config import DIMENSIONS
from huntloop.scoring.dimensions import (
    DimensionScore,
    ScoringResponse,
    build_scoring_user_message,
    score_dimensions,
)


class RecordingClient:
    """A fake OpenAI client that returns a preset response and records arguments."""
    
    def __init__(self, response_dict: dict) -> None:
        self.response_dict = response_dict
        self.calls: list[dict] = []
        
    class Completions:
        def __init__(self, parent):
            self.parent = parent
            
        def create(self, **kwargs):
            self.parent.calls.append(kwargs)
            
            import json
            from collections import namedtuple
            Choice = namedtuple("Choice", ["message"])
            Message = namedtuple("Message", ["content"])
            Usage = namedtuple("Usage", ["prompt_tokens", "completion_tokens"])
            
            choice = Choice(message=Message(content=json.dumps(self.parent.response_dict)))
            
            # The client mock just needs to look like the openai response shape
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
            eligible_countries=["US", "GB"],
            eligible_regions=["EMEA"],
        ),
        compensation_floor=CompensationFloor(
            amount="120000",
            currency="USD",
            period="annual",
        ),
    )


@pytest.fixture
def default_listing():
    return RawListing(
        external_id="ext-1",
        url="https://example.com/job/1",
        title="Senior Software Engineer",
        description_plain="We are looking for a Python engineer...",
        description_html="<p>We are looking for a Python engineer...</p>",
        location_raw="New York, NY",
        comp_raw="$130k - $150k",
    )


class TestDimensions:
    
    def test_dimensions_match_config(self):
        """Test that every dimension in config is a required field on ScoringResponse."""
        for dim in DIMENSIONS:
            assert dim.name in ScoringResponse.model_fields
            assert ScoringResponse.model_fields[dim.name].is_required()

    def test_prompt_requests_the_schema_shape(self):
        """The prompt's response shape MUST validate against ScoringResponse.

        Found live at the 02-12 checkpoint: the prompt requested a nested
        {"dimensions": {...}} wrapper the schema rejects, so every REAL model
        call failed validation while mocked tests (which construct the schema
        shape directly) stayed green. This test substitutes int|null/str
        placeholders with valid values and round-trips the prompt's example
        through the model.
        """
        import json as json_lib
        import re

        from huntloop.scoring.config import SCORING_PROMPT

        match = re.search(
            r"Respond with JSON strictly matching:\n(\{.*?\})\n", SCORING_PROMPT, re.DOTALL
        )
        assert match, "prompt must state its JSON response shape"
        shape = json_lib.loads(match.group(1))
        assert "dimensions" not in shape, "prompt must not request a nested wrapper"
        # Substitute placeholders with valid values and validate against the schema
        filled = {
            k: ({"score": 3, "reason": "r"} if isinstance(v, dict) else "s")
            for k, v in shape.items()
        }
        parsed = ScoringResponse.model_validate(filled)
        assert parsed.to_dimension_scores()["role_fit"] == 3

    def test_nested_dimensions_wrapper_is_unwrapped(self):
        """A model that wraps the dimensions anyway (prompt-following varies)
        must still validate rather than silently unscore the listing."""
        response_dict = {
            "dimensions": {
                "role_fit": {"score": 4, "reason": "Good match"},
                "seniority_fit": {"score": 3, "reason": "Matches"},
                "employer_fit": {"score": 3, "reason": "Fine"},
                "trajectory": {"score": 4, "reason": "Growing"},
            },
            "summary": "Nested but valid",
        }
        parsed = ScoringResponse.model_validate(response_dict)
        assert parsed.role_fit.score == 4
        assert parsed.summary == "Nested but valid"
            
    def test_score_dimensions_success(self, default_listing, default_criteria):
        response_dict = {
            "role_fit": {"score": 4, "reason": "Good python match"},
            "seniority_fit": {"score": 3, "reason": "Title matches"},
            "employer_fit": {"score": 3, "reason": "Seems fine"},
            "trajectory": {"score": 4, "reason": "Good growth potential"},
            "summary": "Solid candidate",
        }
        client = RecordingClient(response_dict)
        response, call = score_dimensions(client, default_listing, default_criteria)
        
        assert response.role_fit.score == 4
        assert response.role_fit.reason == "Good python match"
        assert response.summary == "Solid candidate"
        
        assert hasattr(response, "role_fit")
        assert hasattr(response, "seniority_fit")
        assert hasattr(response, "employer_fit")
        assert hasattr(response, "trajectory")
        
    def test_missing_dimension_raises(self, default_listing, default_criteria):
        # Missing 'trajectory'
        response_dict = {
            "role_fit": {"score": 4, "reason": "Good python match"},
            "seniority_fit": {"score": 3, "reason": "Title matches"},
            "employer_fit": {"score": 3, "reason": "Seems fine"},
            "summary": "Solid candidate",
        }
        client = RecordingClient(response_dict)
        with pytest.raises(LlmResponseError):
            score_dimensions(client, default_listing, default_criteria)
            
    def test_score_out_of_range_raises(self, default_listing, default_criteria):
        response_dict = {
            "role_fit": {"score": 7, "reason": "Good python match"},  # Invalid score
            "seniority_fit": {"score": 3, "reason": "Title matches"},
            "employer_fit": {"score": 3, "reason": "Seems fine"},
            "trajectory": {"score": 4, "reason": "Good growth potential"},
            "summary": "Solid candidate",
        }
        client = RecordingClient(response_dict)
        with pytest.raises(LlmResponseError):
            score_dimensions(client, default_listing, default_criteria)
            
    def test_null_score_accepted(self, default_listing, default_criteria):
        response_dict = {
            "role_fit": {"score": 4, "reason": "Good python match"},
            "seniority_fit": {"score": 3, "reason": "Title matches"},
            "employer_fit": {"score": None, "reason": "Not enough info"},
            "trajectory": {"score": 4, "reason": "Good growth potential"},
            "summary": "Solid candidate",
        }
        client = RecordingClient(response_dict)
        response, call = score_dimensions(client, default_listing, default_criteria)
        
        assert response.employer_fit.score is None
        assert response.employer_fit.reason == "Not enough info"
        
    def test_empty_reason_with_score_defaulted(self):
        # Validate directly via pydantic
        dim = DimensionScore(score=4, reason="")
        assert dim.reason == "no reason supplied by model"
        
    def test_empty_reason_with_null_score_defaulted(self):
        # Validate directly via pydantic
        dim = DimensionScore(score=None, reason="")
        assert dim.reason == "posting gave no evidence for this dimension"
        
    def test_overall_key_is_dropped(self, default_listing, default_criteria):
        """The model MUST NOT produce an overall score, but if it does, it's dropped."""
        response_dict = {
            "role_fit": {"score": 4, "reason": "Good python match"},
            "seniority_fit": {"score": 3, "reason": "Title matches"},
            "employer_fit": {"score": 3, "reason": "Seems fine"},
            "trajectory": {"score": 4, "reason": "Good growth potential"},
            "summary": "Solid candidate",
            "overall": 4.5,  # The offending key
        }
        client = RecordingClient(response_dict)
        response, call = score_dimensions(client, default_listing, default_criteria)
        
        assert not hasattr(response, "overall")
        assert "overall" not in response.model_dump()
        
    def test_uses_scoring_model(self, default_listing, default_criteria):
        response_dict = {
            "role_fit": {"score": 4, "reason": "R"},
            "seniority_fit": {"score": 3, "reason": "R"},
            "employer_fit": {"score": 3, "reason": "R"},
            "trajectory": {"score": 4, "reason": "R"},
            "summary": "S",
        }
        client = RecordingClient(response_dict)
        score_dimensions(client, default_listing, default_criteria)
        
        # Check that it used the scoring model, not triage model
        model_used = client.calls[0]["model"]
        from huntloop.config import load_config
        assert model_used == load_config().scoring_model
        
    def test_system_message_is_constant(self, default_listing, default_criteria):
        response_dict = {
            "role_fit": {"score": 4, "reason": "R"},
            "seniority_fit": {"score": 3, "reason": "R"},
            "employer_fit": {"score": 3, "reason": "R"},
            "trajectory": {"score": 4, "reason": "R"},
            "summary": "S",
        }
        client = RecordingClient(response_dict)
        score_dimensions(client, default_listing, default_criteria)
        
        messages = client.calls[0]["messages"]
        system_msg = next(m["content"] for m in messages if m["role"] == "system")
        from huntloop.scoring.config import SCORING_PROMPT
        assert system_msg == SCORING_PROMPT
        
    def test_output_shapes(self, default_listing, default_criteria):
        response_dict = {
            "role_fit": {"score": 4, "reason": "R1"},
            "seniority_fit": {"score": None, "reason": "R2"},
            "employer_fit": {"score": 3, "reason": "R3"},
            "trajectory": {"score": 4, "reason": "R4"},
            "summary": "S",
        }
        client = RecordingClient(response_dict)
        response, call = score_dimensions(client, default_listing, default_criteria)
        
        score_dims = response.to_score_dimensions()
        assert "role_fit" in score_dims
        assert score_dims["role_fit"] == {"score": 4, "reason": "R1"}
        assert score_dims["seniority_fit"] == {"score": None, "reason": "R2"}
        
        dim_scores = response.to_dimension_scores()
        assert dim_scores == {
            "role_fit": 4,
            "seniority_fit": None,
            "employer_fit": 3,
            "trajectory": 4,
        }
        
    def test_build_user_message_includes_fields(self, default_listing, default_criteria):
        msg = build_scoring_user_message(default_listing, default_criteria)
        
        # Check that required fields are in the user message
        assert default_criteria.profile_summary in msg
        assert default_criteria.seniority_min in msg
        assert default_criteria.seniority_max in msg
        assert "US, GB" in msg
        assert "EMEA" in msg
        assert "120000 USD annual" in msg
        assert default_listing.description_plain in msg
        assert default_listing.comp_raw in msg

