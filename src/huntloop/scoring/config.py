import json
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from huntloop.db.repository import SettingsRepository

@dataclass(frozen=True)
class DimensionDef:
    name: str
    question: str
    guidance: str

SCALE_ANCHORS: dict[int, str] = {
    1: "clear mismatch",
    2: "weak, would need compensating",
    3: "meets the core requirement, nothing distinctive",
    4: "strong match",
    5: "exceptional",
}

DIMENSIONS: tuple[DimensionDef, ...] = (
    DimensionDef("role_fit",
        "Does the actual work match what this person does and wants to do?",
        "Judge the described responsibilities and technologies against the profile summary. "
        "Ignore the job title's seniority marker — that is seniority_fit's job."),
    DimensionDef("seniority_fit",
        "Is the level right? Recorded with direction — stretch and step-down are opposite "
        "meanings, not just distance.",
        "Score the level match. If the role is above the stated range, say so explicitly in "
        "the reason so the stretch_role flag can be raised; if below, say so for step_down."),
    DimensionDef("employer_fit",
        "Company type, stage and reputation against stated preference.",
        "Use only what the posting and the employer's own description state. Do not invent "
        "facts about the company from outside knowledge."),
    DimensionDef("trajectory",
        "Does this move the person forward — scope, compensation signal, growth?",
        "Judge forward movement relative to the profile summary, not absolute prestige."),
)

SAMPLING_PARAMS: dict = {"temperature": 0.0, "top_p": 1.0, "n": 1}

TRIAGE_PROMPT = """You are triaging job listings. Decide whether to keep or drop this listing based on the criteria.
Dropping a listing is cheap, but a wrong drop is invisible to the user.
Return keep=true when uncertain — an over-aggressive drop is the failure mode this stage is audited for.
Respond with JSON strictly matching: {"keep": bool, "reason": str}
"""

# The response shape the model is told to produce. It MUST match ScoringResponse
# exactly: the four dimensions FLAT at the top level, no wrapper object, and no
# model-volunteered flags (flags are computed deterministically, SCOR-09). Found
# live at the 02-12 checkpoint: the prompt used to request a nested
# {"dimensions": {...}} wrapper that ScoringResponse rejects, so every real
# model call failed validation and no listing was ever scored — the mocked tests
# returned the schema shape directly and never exercised the prompt.
_SCORING_RESPONSE_SHAPE = json.dumps(
    {**{d.name: {"score": "int|null", "reason": "str"} for d in DIMENSIONS}, "summary": "str"},
    indent=2,
)

SCORING_PROMPT = f"""You are scoring job listings.
Respond with JSON strictly matching:
{_SCORING_RESPONSE_SHAPE}

Scale anchors:
{json.dumps(SCALE_ANCHORS, indent=2)}

Dimensions:
{json.dumps([asdict(d) for d in DIMENSIONS], indent=2)}

Important:
- `score` MUST be null (not 3) when the posting gives no evidence for that dimension.
- Do NOT produce an overall score.
- Do NOT wrap the dimensions in a containing object; the four dimension names are the top-level keys.
"""

def compute_scoring_config_version(triage_prompt: str, scoring_prompt: str, dimensions: tuple[DimensionDef, ...],
                                   scale_anchors: dict[int, str], sampling_params: dict) -> str:
    """The model identifier is deliberately NOT an input. SCOR-10 stamps three independent axes — criteria version, scoring-config version, model — so a broken score trend can be attributed to the right cause. Folding the model in would destroy that distinction (02-RESEARCH.md anti-patterns)."""
    canonical = json.dumps(
        {
            "triage_prompt": triage_prompt,
            "scoring_prompt": scoring_prompt,
            "dimensions": [asdict(d) for d in dimensions],
            "scale_anchors": {str(k): v for k, v in sorted(scale_anchors.items())},
            "sampling": sampling_params,
        },
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]

@dataclass(frozen=True)
class ScoringConfig:
    triage_prompt: str
    scoring_prompt: str
    dimensions: tuple[DimensionDef, ...]
    scale_anchors: dict[int, str]
    sampling_params: dict

    @property
    def version(self) -> str:
        return compute_scoring_config_version(
            self.triage_prompt, self.scoring_prompt, self.dimensions,
            self.scale_anchors, self.sampling_params
        )

_ACTIVE_CONFIG = None

def active_scoring_config() -> ScoringConfig:
    global _ACTIVE_CONFIG
    if _ACTIVE_CONFIG is None:
        _ACTIVE_CONFIG = ScoringConfig(
            triage_prompt=TRIAGE_PROMPT,
            scoring_prompt=SCORING_PROMPT,
            dimensions=DIMENSIONS,
            scale_anchors=SCALE_ANCHORS,
            sampling_params=SAMPLING_PARAMS,
        )
    return _ACTIVE_CONFIG

def persist_scoring_config(session, config: ScoringConfig) -> str:
    """Storing the full body — not just the hash — is what keeps an old score explainable after the prompt changes (TRAK-04, Phase 4). Stored in `settings` rather than a new table because 02-CONTEXT.md forbids reopening Phase 1's certified schema."""
    body = {
        "triage_prompt": config.triage_prompt,
        "scoring_prompt": config.scoring_prompt,
        "dimensions": [asdict(d) for d in config.dimensions],
        "scale_anchors": {str(k): v for k, v in sorted(config.scale_anchors.items())},
        "sampling": config.sampling_params,
        "version": config.version,
        "persisted_at": datetime.now(timezone.utc).isoformat()
    }
    repo = SettingsRepository(session)
    repo.set_value(f"scoring_config:{config.version}", body)
    return config.version

def load_scoring_config(session, version: str) -> dict | None:
    repo = SettingsRepository(session)
    return repo.get_value(f"scoring_config:{version}")
