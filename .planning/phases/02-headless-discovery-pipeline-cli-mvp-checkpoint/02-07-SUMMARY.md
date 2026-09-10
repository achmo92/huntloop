---
plan: 02-07
phase: 02-headless-discovery-pipeline-cli-mvp-checkpoint
status: complete
completed: 2026-09-10
requirements: [SCOR-05, SCOR-06]
---

# 02-07 SUMMARY — Scoring Pipeline

## What was built

Three new modules implementing the model-calling evaluation stages and the pipeline orchestrating them:

### `src/huntloop/scoring/triage.py` (SCOR-05)
- `MAX_TRIAGE_DESCRIPTION_CHARS = 2000` — the cheap pass uses a truncated description.
- `triage_listing` uses the `triage_model` from config.
- Fail-open bias: network/schema errors result in `keep=True` with a recorded `reason`.

### `src/huntloop/scoring/dimensions.py` (SCOR-06)
- `MAX_SCORING_DESCRIPTION_CHARS = 12000` — the full pass reads up to 12k characters.
- `score_dimensions` returns a tuple: `tuple[ScoringResponse, LlmCall]`. This allows the pipeline to accumulate token usage and access the call details.
- Returns four mandatory dimensions (`role_fit`, `seniority_fit`, `employer_fit`, `trajectory`), each with a 1-5 score (or None) and a non-empty one-line reason.
- A model-volunteered `overall` score is structurally dropped via `extra="ignore"` on `ScoringResponse`.

### `src/huntloop/scoring/pipeline.py`
- Enforces the cost-optimised execution graph:
  1. Deterministic Filters
  2. Triage Pass
  3. Full Dimension Scoring
  4. Aggregate / Flags computation

## Implementation Details & Contract Outputs

### `ScoredListing` Fields
The `ScoredListing` dataclass outputted by `score_listing` defines the contract for 02-09 (persistence) and 02-10 (graph orchestration):
```python
@dataclass(frozen=True)
class ScoredListing:
    listing: RawListing
    scored: bool
    tier_reached: FilterTier
    overall: Decimal | None = None
    dimensions: dict | None = None        # to_score_dimensions() shape
    flags: dict | None = None             # compute_flags() shape
    summary: str = ""                     
    criteria_version: int | None = None   
    rubric_version: str | None = None     
    model: str | None = None              
    drop_reason: str = ""
    error: str = ""
    note: str = ""
    usage: tuple[LlmUsage, ...] = ()
```

### `FilterTier` Members Used
The pipeline accurately maps drops to the `FilterTier` enum from `models.py`:
- Deterministic drops: `FilterTier.DETERMINISTIC`
- Triage drops (or scoring LlmResponseError): `FilterTier.TRIAGE`
- Successful scores: `FilterTier.FULL`

### Normalisation Reuse
- `apply_deterministic_filters` returns a `FilterOutcome` containing the already-built `NormalizedLocation`.
- The pipeline reuses `outcome.location` passing it directly to `compute_flags` to avoid duplicate normalisation.
- `normalize_compensation` is called exactly once in `score_listing` just prior to flag computation.

## Test Results

| Selector | Tests | Status |
|----------|-------|--------|
| `test_dimensions.py` | 13 | ✅ |
| `test_graph.py` | 11 | ✅ |
| `test_openai_import_is_confined_to_llm_client` | 1 | ✅ |
| `tests/ -m "not postgres"` | 246 | ✅ |

## Self-Check: PASSED
- [x] Triage runs on the cheap model, truncated posting, fails open.
- [x] Scoring returns four dimensions with reasons, rejects missing/out-of-range dimensions.
- [x] `overall` score computation belongs strictly to `aggregate.py`.
- [x] Graph order enforced and asserted: a deterministic drop costs 0 model calls; a triage drop costs 1 model call.
- [x] `score_dimensions` returns a tuple.
- [x] `ScoredListing` fields documented for next plans.
