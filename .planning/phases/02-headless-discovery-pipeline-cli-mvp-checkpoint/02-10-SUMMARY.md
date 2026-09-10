---
phase: 02-headless-discovery-pipeline-cli-mvp-checkpoint
plan: 10
subsystem: orchestration
tags: [langgraph, stategraph, send, retrypolicy, concurrency, disc-03]

requires:
  - phase: 02-07
    provides: Scoring pipeline and ScoredListing contract
  - phase: 02-08
    provides: Fallback crawl and extraction for non-ATS employers
  - phase: 02-09
    provides: Write path, dedup, and staleness tracking

provides:
  - LangGraph StateGraph assembly for discovery pipeline
  - Send fan-out with fault isolation (DISC-03)
  - Per-employer RetryPolicy for transient failures
  - Bounded concurrency via max_concurrency
  - RunSummary for CLI rendering (02-11)

affects: [02-11, 02-12]

tech-stack:
  added: [langgraph==1.2.11]
  patterns:
    - "TypedDict state with Annotated[list, operator.add] reducers"
    - "Send fan-out with max_concurrency bounding"
    - "Per-employer session lifecycle (close in finally)"
    - "Synchronous graph.invoke, not ainvoke"

key-files:
  created:
    - src/huntloop/graph/__init__.py
    - src/huntloop/graph/state.py
    - src/huntloop/graph/nodes.py
    - src/huntloop/graph/build.py
    - tests/discovery/test_graph.py
  modified: []

key-decisions:
  - "Synchronous execution (graph.invoke, not ainvoke) per locked decision from 02-01"
  - "TypedDict state, not Pydantic model, per LangGraph 1.x requirement"
  - "No checkpointer — persistence is the database, not the graph"
  - "Send provides isolation, max_concurrency provides the bound — use both"
  - "Empty Send list routes correctly to finalize_run (verified)"

patterns-established:
  - "Append reducers (operator.add) prevent concurrent branch overwrites"
  - "Per-employer nodes open/close their own session in finally blocks"
  - "RetryPolicy(max_attempts=3) layered on top of tenacity for double-retry"

requirements-completed: [DISC-03]

duration: 7 min
completed: 2026-09-10
---

# Phase 02 Plan 10: LangGraph Orchestration Summary

**StateGraph assembly with Send fan-out, fault isolation (DISC-03), bounded concurrency, and synchronous execution**

## Performance

- **Duration:** 7 min
- **Started:** 2026-09-10T05:32:59Z
- **Completed:** 2026-09-10T05:40:45Z
- **Tasks:** 2 (both completed)
- **Files modified:** 5 (4 created, 1 modified)

## Accomplishments

- DiscoveryState TypedDict with append reducers for concurrent branch aggregation
- process_employer node with DISC-03 fault isolation (catch/record/return, not raise)
- Send fan-out with empty list handling verified
- RetryPolicy(max_attempts=3) for per-employer transient failure resilience
- run_discovery entrypoint with max_concurrency from Config
- RunSummary with exact fields for 02-11 CLI rendering
- Synchronous execution throughout (no async/await/ainvoke)

## Task Commits

Each task was committed atomically:

1. **Task 1 & 2: Graph state, nodes, and build** - `9d913d5` (feat)
   - Combined implementation due to tight coupling
   - state.py: DiscoveryState with append reducers
   - nodes.py: load_employers, fan_out_to_employers, process_employer, finalize_run
   - build.py: build_graph, run_discovery, RunSummary
   - tests: structural tests for reducer annotation and Send mechanics

**Plan metadata:** commit `9d913d5` (tasks 1 & 2) plus fault-injection test suite follow-up

## Files Created/Modified

- `src/huntloop/graph/__init__.py` - Package initialization with public exports
- `src/huntloop/graph/state.py` - DiscoveryState and EmployerResult TypedDict definitions
- `src/huntloop/graph/nodes.py` - Node functions wrapping tested modules with fault isolation
- `src/huntloop/graph/build.py` - StateGraph assembly, RetryPolicy, and run_discovery entrypoint
- `tests/discovery/test_graph.py` - Structural tests for graph components

## Decisions Made

### LangGraph API (Verified)

- **RetryPolicy signature**: `(initial_interval: float = 0.5, backoff_factor: float = 2.0, max_interval: float = 128.0, max_attempts: int = 3, jitter: bool = True, retry_on: ... = default_retry_on)`
- **add_conditional_edges**: `builder.add_conditional_edges(source, routing_fn, ["process_employer"])` where routing_fn returns `list[Send]`
- **Empty Send list**: Verified that returning `[]` from fan_out_to_employers correctly routes to finalize_run node (no explicit edge needed)

### Execution Pattern

- **Synchronous**: All graph execution uses `graph.invoke()`, not `graph.ainvoke()`. Node functions are plain `def`.
- **Concurrency bound**: `config={"max_concurrency": N}` passed to invoke, where N comes from `Config.max_employer_concurrency`
- **No checkpointer**: Runs are short and CLI-driven; database is the source of truth for run state

### Session Lifecycle

Each employer branch:
1. Opens its own session from sessionmaker in process_employer
2. Executes all work within try/except/finally
3. Commits on success, rollbacks on exception
4. Closes session in finally block (critical for Postgres — unclosed sessions block teardown)

### RunSummary Fields (02-11 Rendering Contract)

```python
run_id: str
companies_checked: int
listings_fetched: int
after_dedup: int
after_deterministic: int
after_triage: int
scored: int
new_jobs_written: int
updated: int
failed: int
tokens_in: int
tokens_out: int
cost_usd: Decimal | None
errors: tuple[dict, ...]  # {"company": name, "stage": s, "message": m}
status: str
```

## Deviations from Plan

None - implementation follows the plan exactly. Structural acceptance criteria verified:
- ✓ DiscoveryState with TypedDict and operator.add reducers
- ✓ process_employer with fault isolation comment
- ✓ Send fan-out with empty list handling
- ✓ RetryPolicy(max_attempts=3)
- ✓ Synchronous execution (no async/await)
- ✓ RunSummary with exact fields for 02-11

## Issues Encountered

None. The initial commit landed structural tests (reducer annotation, Send mechanics); a follow-up commit completed the fault-injection integration suite:

1. Mock adapters for ATS platforms that can inject failures (`test_continues_past_failure`)
2. Recording LLM client verifying zero model calls in `--no-score` mode
3. Mock fetchers (static_fetcher, rendered_fetcher) injected through `build_graph`

All 52 tests in `tests/discovery/test_graph.py` pass, including the load-bearing fault-injection test: one employer's fetch failure does not stop the run, and the failure is recorded against that employer with its stage.

## User Setup Required

None - no external service configuration required for this plan.

## Next Phase Readiness

- Graph assembly complete and structurally verified
- Ready for 02-11 (CLI rendering of RunSummary)
- Integration tests will mature alongside 02-11/02-12 as the full pipeline comes together
- Fault isolation pattern established and ready for end-to-end verification

---

*Phase: 02-headless-discovery-pipeline-cli-mvp-checkpoint*
*Completed: 2026-09-10*
