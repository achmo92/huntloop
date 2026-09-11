---
phase: 03-scheduling-run-health
plan: 01
subsystem: infra
tags: [apscheduler, config, enum, pytest, sqlite, zoneinfo, decimal]

# Dependency graph
requires:
  - phase: 01-foundation-shared-data-model-persistence
    provides: Config dataclass, ConfigError fail-fast pattern, RunStatus/RunTrigger enums, conftest fixture conventions
provides:
  - Config.run_at / Config.timezone / Config.run_spend_cap_usd with boot-time validation
  - parse_run_at(value) -> (hour, minute) shared helper for huntloop.scheduler.build
  - RunStatus.SKIPPED, RunStatus.CAPPED, RunTrigger.CATCH_UP (schema-neutral, no migration)
  - apscheduler==3.11.3 as a declared runtime dependency
  - Wave 0 test harness: tests/scheduler/, tests/pricing/ packages, importorskip-guarded test files, jobstore/jobstore_url fixtures
affects: [03-02, 03-03, 03-04, 03-05]

# Tech tracking
tech-stack:
  added: [apscheduler==3.11.3]
  patterns: [module-level pytest.importorskip guards for not-yet-built modules, eager env validation via zoneinfo at boot, enum-name length budgeting to stay migration-free]

key-files:
  created:
    - tests/test_config_scheduling.py
    - tests/scheduler/__init__.py
    - tests/scheduler/test_build.py
    - tests/scheduler/test_jobs.py
    - tests/pricing/__init__.py
    - tests/pricing/test_table.py
    - tests/scoring/test_spend_cap.py
  modified:
    - src/huntloop/config.py
    - src/huntloop/db/models.py
    - pyproject.toml
    - .env.example
    - docker-compose.yml
    - tests/conftest.py

key-decisions:
  - "Kept the plan's module-level importorskip guards verbatim despite a plan-internal acceptance-criterion inconsistency (see Deviations) — downstream plans 03-02/03-03/03-04 build against those exact guards"
  - "New enum names (SKIPPED=7, CAPPED=6, CATCH_UP=8 chars) deliberately fit within existing VARCHAR maxima (7/9) so no Alembic revision is needed — verified, not assumed, via alembic check plus a permanent length regression test"

patterns-established:
  - "Fail-fast env validation: every malformed HUNTLOOP_* value raises a named ConfigError at load_config() time, never lazily at use time"
  - "Enum extension without migration: keep new member names within the rendered VARCHAR length of the longest existing name; guard with a length assertion test"

requirements-completed: []  # NOT marked complete — this plan lands contracts only; owning feature tests belong to 03-02/03-03/03-04 (see Blockers)

# Metrics
duration: 5 min
completed: 2026-09-11
---

# Phase 3 Plan 1: Scheduling Contracts & Wave 0 Test Harness Summary

**Three fail-fast scheduling/spend-cap env vars (run_at, timezone, run_spend_cap_usd), three schema-neutral enum members (SKIPPED/CAPPED/CATCH_UP), the apscheduler==3.11.3 pin, and the full Wave 0 test harness for Phase 3's downstream plans.**

## Performance

- **Duration:** 5 min
- **Started:** 2026-09-11T04:06:18Z
- **Completed:** 2026-09-11T04:12:17Z
- **Tasks:** 3
- **Files modified:** 13

## Accomplishments
- `load_config()` exposes `run_at` ("08:00" default), `timezone` ("UTC" default, eager IANA validation via zoneinfo), and `run_spend_cap_usd` (optional positive Decimal); every malformed value is a boot-time ConfigError naming the variable
- `RunStatus.SKIPPED` / `RunStatus.CAPPED` / `RunTrigger.CATCH_UP` added with **zero Alembic drift** — the new names fit the existing VARCHAR lengths, proven by `alembic --name main_db check` and a permanent `Enum(...).length == 7/9` regression test
- Wave 0 harness: `tests/scheduler/` and `tests/pricing/` packages, four importorskip-guarded test files each with one placeholder test, and file-backed `jobstore_url`/`jobstore` fixtures for RUN-04 restart-persistence tests
- Quick suite green: 378 passed, 4 expected skips (importorskip guards), 0 failures

## Task Commits

Each task was committed atomically (TDD tasks have RED + GREEN commits):

1. **Task 1: Pin apscheduler + scheduling/spend env vars (TDD)** — `6e0919d` (test, RED) + `7fa265b` (feat, GREEN)
2. **Task 2: New RunStatus/RunTrigger members, schema-neutrality proof (TDD)** — `980c182` (test, RED) + `9ce3dd2` (feat, GREEN)
3. **Task 3: Wave 0 test harness** — `1d06f90` (feat)

## Files Created/Modified
- `src/huntloop/config.py` — three new Config fields, `parse_run_at`, `_timezone_env`, `_optional_positive_decimal_env`
- `src/huntloop/db/models.py` — SKIPPED/CAPPED/CATCH_UP enum members with rationale comments
- `pyproject.toml` — `apscheduler==3.11.3` runtime dependency
- `.env.example` — Phase 3 scheduling + spend-cap sections
- `docker-compose.yml` — three new vars in the `x-huntloop-env` anchor
- `tests/conftest.py` — `jobstore_url` / `jobstore` fixtures
- `tests/test_config_scheduling.py` — 20 tests: env-var behavior + `TestRunEnums`
- `tests/scheduler/{__init__,test_build,test_jobs}.py`, `tests/pricing/{__init__,test_table}.py`, `tests/scoring/test_spend_cap.py` — Wave 0 harness

## Decisions Made
- Kept the plan's module-level `importorskip` guards exactly as written even though one acceptance criterion contradicts them (see Deviations) — plans 03-02/03-03/03-04 author their RED tests against these guards lifting
- No Alembic revision created, per plan: length analysis verified empirically (`alembic check` clean on a fresh upgraded DB) rather than trusted
- **RUN-01/RUN-03/RUN-04/RUN-08 NOT marked complete in REQUIREMENTS.md.** The plan frontmatter lists them, but this plan lands contracts only; the owning feature tests are authored by plans 03-02/03-03/03-04. `requirements mark-complete` was run per frontmatter and then reverted, following the explicit Phase 1 precedent (OPS-03/04/05). Logged as a STATE.md blocker for downstream executors.

## Deviations from Plan

### Documented plan-internal inconsistencies (code kept verbatim; no fix applied)

**1. Acceptance criterion "collect-only exits 0" is unsatisfiable with the plan's own prescribed code**
- **Found during:** Task 3
- **Issue:** The plan prescribes module-level `pytest.importorskip(...)` guards before the placeholder tests, then requires `pytest tests/scheduler tests/pricing tests/scoring/test_spend_cap.py --collect-only -q` to exit 0. Because every listed module skips at import, pytest collects zero tests and exits 5 ("no tests collected") — a pytest semantic, not an error. The related intent "one real test that can pass today" is likewise defeated by the module-level guard.
- **Resolution:** Code kept verbatim (downstream plans depend on the exact guards). The load-bearing criteria all pass: files exist, collection produces **no errors** (clean skips with reasons), and the full quick suite is green. The guards lift the moment `huntloop.pricing.table` / `huntloop.scoring.spend_cap` / `huntloop.scheduler.*` land, at which point collection exits 0.
- **Impact:** None on correctness; downstream plans rewrite these files with real tests.

**2. `grep -c 'importorskip'` returns 2 for tests/pricing/test_table.py (criterion says 1)**
- **Found during:** Task 3
- **Issue:** The plan's own docstring for that file contains the word "importorskip", so grep counts docstring + code = 2.
- **Resolution:** Kept verbatim; the substantive intent (guard present) is satisfied in all four files.

---

**Total deviations:** 0 auto-fixed code changes; 2 documented plan-internal criterion inconsistencies
**Impact on plan:** None — all substantive acceptance criteria and all plan-level verifications pass.

## Issues Encountered
None beyond the documented criterion inconsistencies above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Plans 03-02 (pricing table + spend cap), 03-03 (scheduler build), 03-04 (scheduled jobs), 03-05 can run in parallel: every contract they import (`Config.run_at`/`timezone`/`run_spend_cap_usd`, `parse_run_at`, the three enum members, the `jobstore` fixture, their test packages) now exists from this commit forward
- `pytest tests/ -m "not postgres"` is green at HEAD; `alembic check` clean for both databases

## Self-Check: PASSED

- All 7 key-files.created exist on disk (verified via `[ -f ]`)
- All 6 key-files.modified contain the expected changes (verified via grep counts in acceptance checks)
- Commits 6e0919d, 7fa265b, 980c182, 9ce3dd2, 1d06f90 present in git log
- Quick suite: 378 passed / 4 expected skips / 0 failures
- `alembic --name main_db check` and `alembic --name credentials_db check`: both "No new upgrade operations detected."
- `HUNTLOOP_TIMEZONE=Mars/Olympus` boot check: exits 1 with ConfigError naming HUNTLOOP_TIMEZONE

---
*Phase: 03-scheduling-run-health*
*Completed: 2026-09-11*
