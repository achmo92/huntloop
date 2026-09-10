---
phase: 02
slug: headless-discovery-pipeline-cli-mvp-checkpoint
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-09-09
---

# Phase 02 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest==9.1.1` (per STACK.md) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` |
| **Quick run command** | `pytest tests/ -x -q` |
| **Full suite command** | `pytest tests/ -q` |
| **Estimated runtime** | ~20 seconds (measured) |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x -q`
- **After every plan wave:** Run `pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green, plus a manual one-time live-endpoint smoke check (see Manual-Only Verifications)
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 02-02 T2 | 02-02 | 2 | DISC-01 | integration (recorded fixtures) | `pytest tests/discovery/test_ats_adapters.py -k fetch -x` | ✅ | ✅ pass |
| 02-02 T2 | 02-02 | 2 | DISC-02 | unit | `pytest tests/discovery/test_ats_adapters.py -k description -x` | ✅ | ✅ pass |
| 02-10 T1 | 02-10 | 5 | DISC-03 | unit (fault injection on a `Send` branch) | `pytest tests/discovery/test_graph.py -k continues_past_failure -x` | ✅ | ✅ pass |
| 02-02 T1 | 02-02 | 2 | DISC-04 | unit | `pytest tests/discovery/test_ats_adapters.py -k zero_vs_error -x` | ✅ | ✅ pass |
| 02-08 T2 | 02-08 | 4 | DISC-05 | unit (mocked PageFetcher + mocked LLM client) | `pytest tests/discovery/test_fallback_crawl.py -x` | ✅ | ✅ pass |
| 02-09 T1 | 02-09 | 4 | DISC-06 | integration (against Phase 1's schema) | `pytest tests/discovery/test_dedup.py -x` | ✅ | ✅ pass |
| 02-06 T3 | 02-06 | 3 | REG-01 | unit | `pytest tests/registry/test_resolution.py -k add_employer -x` | ✅ | ✅ pass |
| 02-06 T3 | 02-06 | 3 | REG-02 | unit (fixtures modeled on cobalt.io/newrocket/spotify/ramp cases) | `pytest tests/registry/test_resolution.py -x` | ✅ | ✅ pass |
| 02-06 T2 | 02-06 | 3 | REG-03 | unit (NewRocket-shaped fixture) | `pytest tests/registry/test_resolution.py -k slug_differs -x` | ✅ | ✅ pass |
| 02-09 T3 | 02-09 | 4 | REG-05 | unit | `pytest tests/registry/test_staleness.py -x` | ✅ | ✅ pass |
| 02-05 T1 | 02-05 | 3 | SCOR-01 | unit | `pytest tests/scoring/test_filters.py -k posting_age -x` | ✅ | ✅ pass |
| 02-03 T2 | 02-03 | 2 | SCOR-02 | unit (fixtures per platform from live schemas) | `pytest tests/scoring/test_location.py -x` | ✅ | ✅ pass |
| 02-05 T1 | 02-05 | 3 | SCOR-03 | unit | `pytest tests/scoring/test_filters.py -k geography -x` | ✅ | ✅ pass |
| 02-03 T3 | 02-03 | 2 | SCOR-04 | unit | `pytest tests/scoring/test_compensation.py -x` | ✅ | ✅ pass |
| 02-07 T3 | 02-07 | 3 | SCOR-05 | unit (mocked OpenAI-compatible client, asserts call order/model) | `pytest tests/scoring/test_graph.py -k triage_before_score -x` | ✅ | ✅ pass |
| 02-07 T2 | 02-07 | 3 | SCOR-06 | unit (mocked model response) — dimensions locked in 02-04 as `role_fit`, `seniority_fit`, `employer_fit`, `trajectory` | `pytest tests/scoring/test_dimensions.py -x` | ✅ | ✅ pass |
| 02-04 T3 | 02-04 | 2 | SCOR-07 | unit | `pytest tests/scoring/test_aggregate.py -k overall -x` | ✅ | ✅ pass |
| 02-04 T3 | 02-04 | 2 | SCOR-08 | unit | `pytest tests/scoring/test_aggregate.py -k renormalize -x` | ✅ | ✅ pass |
| 02-05 T2 | 02-05 | 3 | SCOR-09 | unit | `pytest tests/scoring/test_flags.py -x` | ✅ | ✅ pass |
| 02-04 T2 | 02-04 | 2 | SCOR-10 | unit | `pytest tests/scoring/test_versioning.py -x` | ✅ | ✅ pass |
| 02-04 T3 | 02-04 | 2 | SCOR-11 | integration (asserts mock LLM client call count == 0) | `pytest tests/scoring/test_aggregate.py -k backlog_recompute -x` | ✅ | ✅ pass |
| 02-09 T2 | 02-09 | 4 | TRAK-06 | integration | `pytest tests/discovery/test_write_path.py -k low_scorer -x` | ✅ | ✅ pass |
| 02-04 T1 | 02-04 | 2 | OPS-06 | unit | `pytest tests/scoring/test_client_routing.py -x` | ✅ | ✅ pass |

*Status legend: ✅ pass · ⚠️ flaky. All 23 rows verified green by 02-12 Task 2 on 2026-09-10.*

*Task ID / Plan / Wave assigned during planning. `File Exists` and `Status` are updated by the executor as each plan lands; 02-12 Task 2 runs the full contract and closes the map.*

---

## Wave 0 Requirements

*All Wave 0 work is plan **02-01** (wave 1). It runs before every other Phase 2 plan.*

- [x] `tests/conftest.py` — extend the existing Phase 1 conftest with recorded (not live) JSON response fixtures per platform, built from the real response shapes captured in research (Greenhouse `cobaltio`, Lever `spotify`, Ashby `ramp`). *(02-01 Task 2)*
- [x] `tests/discovery/`, `tests/registry/`, `tests/scoring/`, `tests/cli/` — established and populated with tests. *(02-01 Task 1)*
- [x] Framework install: `pytest==9.1.1` is pinned, `postgres` marker is registered, and Phase 2 runtime packages are pinned. *(02-01 Task 1)*
- [x] Recorded HTML fixtures for the resolution-pipeline test cases (cobalt.io static HTML containing the Greenhouse link, newrocket.com static HTML containing the inline XHR call, ramp.com static HTML containing Ashby apply links) — captured in Wave 0. *(02-01 Task 2)*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live-endpoint smoke check across ATS platforms | DISC-01, DISC-02 | Depends on external network/service availability; not suitable for automated CI | Before `/gsd:verify-work`, confirm at least one real employer per ATS still resolves and fetches correctly — reuse cobalt.io (Greenhouse), ramp.com (Ashby), spotify.com/lifeatspotify.com (Lever) as live cases already reproduced in research |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** 2026-09-10
