---
phase: 03-scheduling-run-health
plan: 04
subsystem: scoring
tags: [spend-cap, run-status, decimal-cost, langgraph, runbook]

# Dependency graph
requires:
  - phase: 03-02
    provides: SpendTracker/SpendCapReached, price table, score_listing(spend_tracker=) gating
  - phase: 03-03
    provides: execute_scheduled_run/classify_trigger scheduled path, scheduler test module
provides:
  - runs.cost_usd is a real Decimal sourced from the SpendTracker on every finished run (RUN-05/RUN-07)
  - A tripped cap stops all further model calls; the blocked listing never reaches the jobs table (RUN-08)
  - RunStatus.CAPPED with a human-readable "spend cap" reason; unpriced models disclosed in error_summary
  - EmployerResult.capped flag semantics (budget stop ≠ failure; never PARTIAL on its own)
affects: [03-05 run-history renderer, phase 4 pipeline interface, phase 5]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Tracker-as-authoritative-ledger: finalize_run/run_discovery read cost+tokens from the SpendTracker, never from per-employer sums (per-employer sums structurally miss the triage usage of a cap-blocked listing)"
    - "Cap-blocked listings are dropped from _ScoreBatchOut.listings rather than flagged — write_listing persists unconditionally, so dropping is the only correct mechanism"

key-files:
  created: []
  modified:
    - src/huntloop/graph/state.py
    - src/huntloop/graph/nodes.py
    - src/huntloop/graph/build.py
    - tests/discovery/test_graph.py
    - tests/discovery/test_write_path.py
    - tests/scheduler/test_jobs.py

key-decisions:
  - "run_status checks errors BEFORE the cap: PARTIAL beats CAPPED — a genuine failure is more actionable than a budget stop"
  - "run_discovery always constructs a SpendTracker (cap_usd=None means measure-never-stop) so cost accounting exists even with no cap configured"
  - "The crashed-run FAILED path reports the tracker's real spend — a run that crashed after spending money still spent it"

patterns-established:
  - "Cap semantics: a cap is a deliberate budget stop recorded as a status, never as an employer error (DISC-03 isolation must not be triggered by budget decisions)"
  - "Cap amounts in tests are derived from the price table via cost_for_usage, never magic numbers"

requirements-completed: [RUN-05, RUN-07, RUN-08]

# Metrics
duration: 20min
completed: 2026-09-11
---

# Phase 3 Plan 4: Spend Cap in the Discovery Run Summary

**SpendTracker threaded through the LangGraph fan-out: every finished run persists a real Decimal cost, a tripped cap halts scoring before the blocked listing can be written, and capped runs read back as RunStatus.CAPPED with a "spend cap" reason**

## Performance

- **Duration:** 20 min
- **Started:** 2026-09-11T05:51:50Z
- **Completed:** 2026-09-11T06:11:44Z
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments
- `runs.cost_usd` is sourced from the tracker's `spent_usd` (Decimal, 6-dp) on every finished run, replacing Phase 2's hardcoded `0` — the same values land on both the Run row and `RunSummary`
- A cap trip inside `_score_batch` breaks the employer's batch and drops the blocked listing before `write_batch` can persist a permanently half-scored row; the employer is marked `capped`, never `errored`
- `run_status` gains CAPPED (errors still win: PARTIAL beats CAPPED); `finalize_run` appends the tracker's cap reason and an unpriced-model disclosure to `error_summary` instead of replacing employer-error text
- Both RUN-08 validation-contract integration tests authored and green: `test_cap_blocked_listing_not_written` (blocked listing derived from the fake client's call log, zero Job rows for it) and `test_run_status_capped_with_reason` (scheduled path, cap amount in reason, bounded cost, undisturbed trigger)

## Task Commits

Each task was committed atomically (TDD where the feature was new):

1. **Task 1: Thread SpendTracker through the graph, stop scoring when it trips** - `aa4c0e9` (test: RED) + `67b4473` (feat: GREEN)
2. **Task 2: Tracker as authoritative cost/token ledger, RunStatus.CAPPED** - `5038a1d` (test: RED) + `563e584` (feat: GREEN)
3. **Task 3: End-to-end cap tests (no production code)** - `677c8a1` (test)

**Plan metadata:** (see final docs commit)

## Files Created/Modified
- `src/huntloop/graph/state.py` - `EmployerResult.capped` flag (budget stop ≠ failure)
- `src/huntloop/graph/nodes.py` - `_score_batch`/`process_employer` accept the tracker; cap trip breaks the batch and drops the blocked listing; `run_status` CAPPED branch; `finalize_run` tracker-sourced cost/tokens and notes-based error_summary
- `src/huntloop/graph/build.py` - `build_graph` forwards the tracker to both nodes; `run_discovery` constructs `SpendTracker(cap_usd=cfg.run_spend_cap_usd)` once per run, reuses one `cfg` (no double `load_config()`), real cost on `RunSummary` and on the crashed-run FAILED path
- `tests/discovery/test_graph.py` - `TestSpendCapInGraph` (3 tests) + `TestSpendCapLedger` (4 tests)
- `tests/discovery/test_write_path.py` - `test_cap_blocked_listing_not_written` + `_PricedFakeClient`
- `tests/scheduler/test_jobs.py` - `test_run_status_capped_with_reason`

## Decisions Made
- Errors outrank the cap in `run_status` (PARTIAL > CAPPED): a real failure is more actionable than a budget stop — as specified in the plan
- The tracker is constructed on every run even without a cap: it is the cost ledger first and the brake second; `cap_usd=None` means "measure, never stop"
- The crashed-run FAILED path reports the tracker's spend rather than zeros
- No `try/except SpendCapReached` around the employer body: `_score_batch` already absorbs it, and catching at employer level would land in the DISC-03 error branch

## Deviations from Plan

None - plan executed exactly as written, with two documented grep-criterion nuances below (plan-internal criterion text vs. the plan's own action text; no code changes needed):

### Acceptance-criterion nuances (not code deviations)

**1. Task 1 criterion `grep -c 'spend_tracker=spend_tracker' nodes.py == 1` returns 2**
- **Reason:** The plan's own action text requires forwarding the tracker in TWO places in nodes.py: `process_employer` → `_score_batch` and `_score_batch` → `score_listing`. Both must be spelled `spend_tracker=spend_tracker`; a count of 1 is structurally impossible given the plan's action steps. Same precedent as 03-01's documented plan-internal criterion inconsistency.

**2. Verification grep `cost_usd=0\b` matches `src/huntloop/db/repository.py:295`**
- **Reason:** That line is `RunRepository.start()`'s initial value on a freshly-created RUNNING row (Phase 1 code, untouched by this plan). It is not a finish-path hardcode: every finished run goes through `finish()` with a real tracker-sourced cost. A RUNNING row's 0 is the honest starting ledger value. Pre-existing and semantically correct; left as-is.

**3. Task 3 plan formula `cap = cost_for_usage(...) * 2 + 0.000001` was interpreted with per-listing (not per-call) tokens**
- **Reason:** The formula's tokens are ambiguous between per-call and per-listing usage; only the per-listing reading satisfies the same plan's stated behaviour "exactly two listings fit under the cap". Implemented as `cap = per_call * 4 + Decimal("0.000001")` (4 calls = 2 listings), which honours the intent and the `cost_for_usage`-derived-cap criterion.

---

**Total deviations:** 0 auto-fixed (3 documented criterion nuances, no code changes required)
**Impact on plan:** None — all truths hold, all validation-contract tests green.

## Issues Encountered
- `alembic --name credentials_db check` initially failed with "Target database is not up to date" against the repo-local `data/credentials.db` — a stale gitignored runtime artifact, not schema drift. Verified clean from a fresh database (upgrade head → check → "No new upgrade operations detected") for both `main_db` and `credentials_db`. No migrations were touched by this plan.

## User Setup Required

None - no external service configuration required.

## Notes for Plan 03-05 (run history renderer)

- **Cost types:** `RunSummary.cost_usd` is a `Decimal` (from `tracker.spent_usd`, quantized to 6 dp via `COST_QUANTUM`). `Run.cost_usd` read back from the DB is also a `Decimal` (`Numeric(12,6)`; verified on SQLite that six-place precision survives the round-trip, e.g. `Decimal('0.012300')`). Format currency from the Decimal directly — never route through float.
- **Capped-run `error_summary` format:** employer-error lines first (if any), then the cap reason — `"stopped: spend cap of $3.00 reached after $3.75 of model spend"` (from `SpendTracker.reason()`), then optionally `"cost is a lower bound: no price table entry for <model, ...>"` for unpriced models. Sections are `\n`-joined; `error_summary` is `None` for a clean uncapped run.
- **CLI exit-code mapping:** the CLI has no per-status exit-code mapping to update — `cli/render.py` renders `summary.status` as text (`Status: capped`) and `main()` exits without consulting it. `RunStatus.CAPPED` needed no additional handling.
- `run_status` value strings: `"capped"` flows into `RunSummary.status` via `run_status(results).value` — the renderer sees it without any new plumbing.

## Next Phase Readiness
- RUN-05 / RUN-07 / RUN-08 are now fully implemented with all owning tests green (RUN-08's integration tests were the last outstanding pair; per the 03-01 requirements-precedent, `requirements mark-complete` is run by this plan now that they are green)
- The 358-test Phase 2 baseline plus all Phase 3 additions pass: 456 tests green including Postgres legs; no test over 4.5s in discovery/scheduler
- Ready for 03-05 (run history renderer), which has exact cost/summary-format notes above

## Self-Check: PASSED

- All 6 modified files exist on disk ✓
- All task commits verified in git log: aa4c0e9, 67b4473, 5038a1d, 563e584, 677c8a1 ✓
- All 4 validation-contract tests green in one run ✓
- Full suite (456) green including Postgres legs ✓

---
*Phase: 03-scheduling-run-health*
*Completed: 2026-09-11*
