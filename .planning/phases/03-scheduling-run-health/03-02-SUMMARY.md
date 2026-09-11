---
phase: 03-scheduling-run-health
plan: 02
subsystem: scoring
tags: [pricing, decimal, thread-safety, spend-cap, tdd, langgraph-concurrency]

# Dependency graph
requires:
  - phase: 03-scheduling-run-health-01
    provides: Wave 0 importorskip test scaffolding (tests/pricing, tests/scoring/test_spend_cap) and Config.run_spend_cap_usd
provides:
  - PRICE_TABLE + cost_for_usage(model, tokens_in, tokens_out) -> Decimal (6 dp) and is_priced()
  - Thread-safe SpendTracker and SpendCapReached signal
  - score_listing(..., spend_tracker=None) enforcing the cap before each of its two model calls
affects: [03-scheduling-run-health-03, 03-scheduling-run-health-04, 03-scheduling-run-health-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Static in-repo price table over a third-party pricing library (unknown model => Decimal(0) + is_priced()=False, never a fabricated price)"
    - "Lock-guarded accumulator for state shared across LangGraph's Send fan-out OS threads"
    - "Cap enforcement by exception (SpendCapReached) rather than drop_reason, so write_batch can never persist a cap-blocked listing"

key-files:
  created:
    - src/huntloop/pricing/__init__.py
    - src/huntloop/pricing/table.py
    - src/huntloop/scoring/spend_cap.py
  modified:
    - src/huntloop/scoring/pipeline.py
    - tests/pricing/test_table.py
    - tests/scoring/test_spend_cap.py

key-decisions:
  - "Kept SpendCapReached uncaught in score_listing: a cap-blocked listing produces no ScoredListing at all, because write_batch persists every ScoredListing it is handed (TRAK-06) and the locked decision is hold-back-and-retry-next-run"
  - "requirements mark-complete run for RUN-07 only; RUN-08's unit tests are green but its integration tests (test_cap_blocked_listing_not_written, test_run_status_capped_with_reason) are owned by 03-03/03-04, so RUN-08 stays open per the 03-01 precedent"

patterns-established:
  - "SpendTracker._reason_locked() is called only with the lock already held — no re-entrant locking in check()/reason()"
  - "Pricing lookups normalise model names via strip().lower() so case differences never yield a false 'unpriced'"

requirements-completed: [RUN-07]  # RUN-08 held open pending 03-03/03-04 integration tests — see Decisions

# Metrics
duration: 47min
completed: 2026-09-11
---

# Phase 3 Plan 2: Pricing Table & Spend Cap Summary

**Static per-model USD price table (Decimal, 6 dp, honest unknown-model fallback) plus a lock-guarded SpendTracker, enforced inside score_listing immediately before both the triage and dimension-scoring model calls**

## Performance

- **Duration:** 47 min
- **Started:** 2026-09-11T04:16:06Z
- **Completed:** 2026-09-11T05:03:26Z
- **Tasks:** 3 (all TDD: RED → GREEN)
- **Files modified:** 6

## Accomplishments
- `huntloop.pricing.table`: PRICE_TABLE (6 OpenAI models, USD per 1M in/out tokens), `cost_for_usage` quantized to the Numeric(12,6) column's 6 dp, `is_priced` for surfacing "unpriced" instead of a fabricated price (RUN-07)
- `huntloop.scoring.spend_cap`: thread-safe `SpendTracker` (lock-guarded token/USD/unpriced-model accumulation, proven lossless under 16 concurrent writers × 100 records) and the `SpendCapReached` signal (RUN-08)
- `score_listing(..., spend_tracker=None)`: cap checked immediately before `triage_listing` AND immediately before `score_dimensions` (Pitfall C — a listing can pass cheap triage and only then push the run over the cap); each call's usage recorded the moment it returns; a trip raises out rather than returning a persistable ScoredListing
- Wave 0 importorskip guards lifted: `pytest tests/pricing tests/scoring/test_spend_cap.py` reports 21 passed, 0 skipped

## Task Commits

Each task was committed atomically (TDD: test commit then feat commit per task):

1. **Task 1: Static per-model USD price table** — `92b70cd` (test) + `4428db3` (feat)
2. **Task 2: Thread-safe SpendTracker and SpendCapReached** — `ffdebdd` (test) + `9a6fad2` (feat)
3. **Task 3: Cap enforcement in score_listing at both call sites** — `3ca1a02` (test) + `cc17950` (feat)

**Plan metadata:** (see final docs commit below)

## Files Created/Modified
- `src/huntloop/pricing/__init__.py` — new package marker
- `src/huntloop/pricing/table.py` — PRICE_TABLE, cost_for_usage, is_priced, COST_QUANTUM
- `src/huntloop/scoring/spend_cap.py` — SpendTracker, SpendCapReached
- `src/huntloop/scoring/pipeline.py` — spend_tracker kwarg, two check()/record() sites, RUN-08 docstring
- `tests/pricing/test_table.py` — 7 tests; importorskip guard removed
- `tests/scoring/test_spend_cap.py` — 14 tests across TestSpendTrackerCheck/Record, thread-safety, reason, and TestScoreListingCapEnforcement

## Decisions Made
- **No refactor commits:** implementations followed the plan's reviewed code verbatim; tests green throughout, nothing to clean up.
- **RUN-07 marked complete, RUN-08 held open:** per the 03-01 precedent recorded in STATE.md, requirements are only marked complete once ALL owning tests are green. RUN-07's two owning tests (test_cost_for_usage_known_model, test_cost_for_usage_unknown_model_falls_back) are green. RUN-08's three unit tests from this plan are green, but its integration tests (tests/discovery/test_write_path.py::test_cap_blocked_listing_not_written, tests/scheduler/test_jobs.py::test_run_status_capped_with_reason) are authored by plans 03-03/03-04 — so `requirements mark-complete RUN-08` was deliberately NOT run; those executors should run it once their tests are green.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- 03-03 (scheduler) and 03-04 can now import `SpendTracker`/`SpendCapReached` and wire `spend_tracker` through build_graph's existing `partial(...)` chain into `_score_batch` → `score_listing`, exactly as 03-RESEARCH.md Pattern 5 specifies.
- `SpendTracker.reason()` is ready for the terminal CAPPED RunStatus reason string (03-04).
- The two skipped tests in `tests/scheduler/` remain intentionally skipped — their modules (`huntloop.scheduler.*`) are owned by 03-03/03-04.

---
*Phase: 03-scheduling-run-health*
*Completed: 2026-09-11*

## Self-Check: PASSED

All key-files exist on disk; all 6 task commits present in git log.
