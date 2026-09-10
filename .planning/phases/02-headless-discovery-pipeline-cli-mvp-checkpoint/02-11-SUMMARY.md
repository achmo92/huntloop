---
phase: 02-headless-discovery-pipeline-cli-mvp-checkpoint
plan: 11
subsystem: cli
tags: [cli, argparse, exit-codes, reg-01, disc-06, trak-06]

requires:
  - phase: 02-03
    provides: Criteria schema and versioned loader
  - phase: 02-06
    provides: Employer resolution (resolve_employer, persist_resolution)
  - phase: 02-09
    provides: Write path, dedup, run repository
  - phase: 02-10
    provides: run_discovery entrypoint and RunSummary

provides:
  - huntloop CLI with company/criteria/run/jobs subcommands
  - Three-valued exit-code contract (EXIT_OK / EXIT_PARTIAL / EXIT_ABORTED)
  - Human and JSON renderers over RunSummary from one field list
  - jobs list including low scorers with min-score filter

affects: [02-12, 03]

tech-stack:
  added: []
  patterns:
    - "Subparser wiring with per-command handler modules"
    - "Exit-code contract consumed by Phase 3 scheduler without text parsing"
    - "One field list drives both human and JSON output"

key-files:
  created:
    - src/huntloop/cli/main.py
    - src/huntloop/cli/company.py
    - src/huntloop/cli/criteria.py
    - src/huntloop/cli/run.py
    - src/huntloop/cli/jobs.py
    - src/huntloop/cli/render.py
    - tests/cli/test_cli.py
  modified: []

key-decisions:
  - "Implementation landed in commit 17ad51c during the 02-10 graph work; audited against plan must-haves in this session with no gaps found"
  - "run.py stays thin (28 lines) — the contract is complete; depth lives in render.py and graph/build.py"
  - "EXIT_PARTIAL iff summary.errors is non-empty; EXIT_ABORTED only on exceptions that prevent completion"
  - "--limit truncates top_listings client-side after the run; --json switches renderers over the same RunSummary"

patterns-established:
  - "cmd_* handlers take args and return exit codes; main() dispatches"
  - "resolve_employer + persist_resolution shared by company add and company import"
  - "JSON output mirrors the human table's field list exactly"

requirements-completed: [REG-01, DISC-06, TRAK-06]

duration: n/a (audit only; implementation pre-existing)
completed: 2026-09-10
---

# Phase 02 Plan 11: CLI Summary

**company/criteria/run/jobs subcommands with the three-valued exit-code contract**

## What was verified

The CLI implementation (landed during the graph work commit) was audited against every plan must-have:

- ✓ `huntloop company add|import|resolve|list` — name or URL registration, YAML bulk import
- ✓ `huntloop criteria load` — version increment shown on load
- ✓ `huntloop run [--no-score] [--json] [--limit] [--concurrency]` — stage-by-stage output
- ✓ `huntloop jobs list` — scored jobs including low scorers, `--min-score` filter, `--json`
- ✓ Exit codes: 0 clean / 1 partial (per-employer failures) / 2 aborted
- ✓ `run_discovery` invoked with `no_score` passthrough (key-link)
- ✓ `resolve_employer` + `persist_resolution` shared by add and import (key-link)
- ✓ `[project.scripts] huntloop = "huntloop.cli.main:main"` (key-link)

## Test results

28/28 CLI tests pass (`tests/cli/test_cli.py`), covering all six must-have truths including
degraded-vs-aborted exit-code distinction and low-scorer visibility. Full suite: 377 passed.

## Deviations from Plan

- `run.py` is 28 lines against a min_lines heuristic of 70. The contract (run_discovery
  invocation, no_score passthrough, trigger stamping, concurrency, limit, renderer switch,
  exit codes) is complete and tested; the depth lives in `render.py` and `graph/build.py`
  where it belongs. Accepted as-is.

## Next Phase Readiness

- CLI smoke: `huntloop --help` exits 0 with all four subcommands
- 02-12 can run the full validation contract and live-endpoint smoke check against this CLI
- Phase 3's scheduler consumes the exit-code contract without parsing output text

*Phase: 02-headless-discovery-pipeline-cli-mvp-checkpoint*
*Completed: 2026-09-10*
