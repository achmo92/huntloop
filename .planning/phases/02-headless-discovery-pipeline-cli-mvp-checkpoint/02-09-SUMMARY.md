---
plan: 02-09
phase: 02-headless-discovery-pipeline-cli-mvp-checkpoint
status: complete
completed: 2026-09-10
requirements: [DISC-06, REG-05, TRAK-06]
---

# 02-09 SUMMARY — Write Path & Persistence

## What was built

The state persistence mechanisms for the headless discovery pipeline. This includes idempotent job updates, run history tracking, and strict staleness tracking for employer registries.

### `src/huntloop/discovery/dedup.py` (DISC-06)
- Implemented `compute_dedup_key` as a pure, deterministic function.
- If an ATS returns an `external_id`, the key is `company_slug:external_id`.
- If an `external_id` is absent (like for crawled listings), the key falls back to `url:normalized_url`, truncating and securely hashing the tail to fit within the `jobs.dedup_key` database index bound (240 chars).
- `normalize_url` safely strips volatile tracking params while preserving defining params (e.g., `gh_jid`). It unifies HTTP/HTTPS schemes to prevent duplication across ATS upgrades.

### `src/huntloop/discovery/write.py` (TRAK-06)
- Persists **all** discovered listings to the database, including ones strictly rejected by deterministic filters. Filter drops receive no scores but get `filter_tier_reached` stamped. This provides complete visibility into filter aggressiveness and prevents re-fetches when tweaking logic.
- Implemented `write_listing` to conditionally upsert Discovery-owned columns and `first_seen_at` (only if the job didn't exist).
- Only passes 1.0/evaluated jobs into the existing `JobRepository.mark_scored()` interface.

### `src/huntloop/registry/staleness.py` (REG-05)
- Tracks employer decay without confusing errors for silence. 
- Only increments a company's `consecutive_empty_runs` upon receiving `FetchStatus.EMPTY` (a **confirmed** empty signal). 
- Network or adapter errors (`FetchStatus.ERROR`) deliberately update the `last_checked_at` timestamp but bypass the counter update.
- Yields appropriate `staleness_message` diagnostics when the count exceeds `Config.stale_after_empty_runs` or when `resolution.status == "needs_reverification"` (e.g. 404/410).

### `src/huntloop/db/repository.py` Updates
- Added `RunRepository` tracking global stats for each run.
- Appended missing tracking columns to `JOB_DISCOVERY_OWNED_COLUMNS`.

## Implementation Details & Contract Outputs

### `JOB_DISCOVERY_OWNED_COLUMNS` Extension
The list required extending to authorize upserting metadata columns without overwriting user state. We added:
- `filter_tier_reached`
- `source_run_id`
- `first_seen_at`

### `WriteOutcome` Shape
`write_batch` processes entire employers safely, tracking results without blowing up the whole batch for a single job error:
```python
@dataclass(frozen=True)
class WriteOutcome:
    inserted: int = 0
    updated: int = 0
    failed: int = 0
    errors: tuple[str, ...] = ()
```

### `RunRepository` Signatures (for 02-10, 02-11)
```python
def start(self, trigger: RunTrigger) -> Run
def record_error(self, run_id: uuid.UUID, company_id: uuid.UUID, stage: str, message: str) -> RunError
def finish(self, run_id: uuid.UUID, *, status: RunStatus, companies_checked: int, listings_fetched: int, after_dedup: int, after_deterministic: int, after_triage: int, scored: int, new_jobs_written: int, tokens_in: int, tokens_out: int, cost_usd: float, error_summary: str | None = None) -> Run
def get(self, run_id: uuid.UUID) -> Run | None
def list_recent(self, limit: int = 10) -> list[Run]
```

### Resolved Enumeration Members
- `RunStatus`: `.RUNNING`, `.SUCCESS`, `.PARTIAL`, `.FAILED`
- `RunTrigger`: `.MANUAL`, `.SCHEDULED`
- `FilterTier`: `.DETERMINISTIC`, `.TRIAGE`, `.FULL`

## Test Results

| Selector | Tests | Status |
|----------|-------|--------|
| `test_dedup.py` | 13 | ✅ |
| `test_dedup.py (sqlite)` | 1 | ✅ |
| `test_write_path.py` | 5 | ✅ |
| `test_staleness.py` | 9 | ✅ |
| `tests/ -m "not postgres"` | 293 | ✅ |

## Self-Check: PASSED
- [x] Unkeyable listings fail loudly, preventing silent arbitrary keys.
- [x] Dedup keys lack UUIDs, times, or titles, remaining purely deterministic.
- [x] Erroneous API paths do not inadvertently increment staleness counters.
- [x] All test suites pass seamlessly, including idempotent writes tested on both SQL paradigms via `portable_engine`.
