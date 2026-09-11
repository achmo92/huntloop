---
phase: 02-headless-discovery-pipeline-cli-mvp-checkpoint
plan: 12
subsystem: phase-closure
tags: [docker, chromium, validation, readme, smoke, mvp-checkpoint, disc-01, disc-02, ops-06]

requires:
  - phase: 02-01 through 02-11
    provides: everything

provides:
  - Chromium in the image with single-command start preserved (OPS-01)
  - app service with huntloop CLI as entrypoint
  - Completed 23-row validation map, all green
  - README clone-to-scored-listing walkthrough
  - scripts/smoke_live.py live-endpoint check
  - MVP checkpoint sign-off

affects: [03, 04, 05]

key-files:
  created:
    - scripts/smoke_live.py
    - .planning/phases/02-headless-discovery-pipeline-cli-mvp-checkpoint/02-VALIDATION.md
  modified:
    - Dockerfile
    - docker-compose.yml
    - README.md

key-decisions:
  - "Live checkpoint found and fixed 8 real bugs no mocked test could catch (see below)"
  - "User's verdict recorded verbatim — Phase 5 tuning starts from it"
  - "Crawl-path quality gaps accepted as known v1 limitations, recorded for gap closure"

requirements-completed: [DISC-01, DISC-02, OPS-06]

completed: 2026-09-11
---

# Phase 02 Plan 12: Phase Closure — MVP Checkpoint Summary

**Chromium in the image, complete validation map, README walkthrough, live smoke, and a human-verified MVP checkpoint.**

## Measured numbers (02-VALIDATION.md budget was an estimate made before code existed)

- **Quick suite runtime:** ~18 seconds (358 tests, non-postgres); full suite ~24s (385+ tests incl. Postgres legs)
- **Image size:** 2.52 GB with Chromium; build ~133s for the Playwright layer
- **Run duration (live):** ~10 minutes for 2 crawl-path employers (8 Chromium renders + extraction + sequential gpt-4o scoring of 5 listings) — Phase 3+ optimization territory

## Live smoke results (2026-09-10, inside the container)

| Platform | Board | Listings | With descriptions | Result |
|----------|-------|----------|-------------------|--------|
| greenhouse | cobaltio | 12 | 12 | PASS |
| lever | spotify | 78 | 78 | PASS |
| ashby | ramp | 146 | 146 | PASS |

Research's 30-day signature window had not expired at phase close.

## The checkpoint did exactly what it exists for

Every automated test in this phase runs against recorded fixtures — deliberately. The
live checkpoint, with a human present, found **eight real bugs** that no mocked test
could catch, all fixed with regression tests during this plan:

1. **Scoring prompt/schema mismatch (02-07)** — the prompt requested a nested
   `{"dimensions": {...}}` wrapper the schema rejects; every real model call failed
   validation silently. **The scoring pipeline had never scored a single real listing.**
2. **RenderedPageFetcher never wired (02-10)** — `run_discovery` never constructed one;
   the crawl path could never render SPA careers pages.
3. **Crawl hash checked before render** — a stored static hash would skip every future
   run of an SPA site as "unchanged".
4. **Render trigger too naive (02-08)** — only <400-char shells rendered; nav-heavy
   shells (Atlassian: 5KB of menus, zero job links) never did.
5. **Locale variants burned the crawl budget** — `/ja/`, `/fr/` variants of the careers
   page consumed all 8 pages before a single listing page.
6. **`run --limit` mutated a frozen dataclass** — defaults to 10, so every run crashed
   at render time.
7. **Extraction truncated at 2000 output tokens** — large boards failed whole-page
   extraction mid-JSON.
8. **Renderer key mismatch + silent scoring failures** — run errors printed as
   `[Unknown] fetch:` with the message dropped; per-listing scoring failures
   vanished from the run summary.

Plus two test-isolation fixes (`.env` leakage into config tests) and the compose
`HUNTLOOP_OPENAI_API_KEY` optional passthrough.

## User's verdict (verbatim — Phase 3/5 tuning starts from this)

> "Scores look good, but need further refining. Those can be taken up at later stage also?"

Answered yes at the checkpoint: criteria tuning via versioned reloads (weight changes
recompute the backlog without model calls, SCOR-11), rubric changes tracked by SCOR-10
versioning, and the dedicated Phase 5 adaptive loop.

Checkpoint observations along the way: the score ranking visibly reflected the user's
profile (IC data roles in Bengaluru scored 3.76-3.85; a people-management role scored
1.76; unassessable dimensions correctly returned None and renormalized away).

## Capabilities the phase shipped without (gap-closure candidates)

- **Name-only employer resolution** — with Firecrawl ruled out, `company add` by bare
  name cannot resolve; a careers URL is required for the crawl path
- **Index-page extraction quality (ServiceNow)** — extraction on jobs-index pages
  stamps the index URL onto listings (pagination looks like new jobs to the dedup key)
  and extracts whatever posting is most prominent ("Executive Assistant"), not the
  board's actual listings. Detail-page-first crawling works (Atlassian); index-style
  boards need per-job link following
- **Posted-date extraction from grid layouts** — Atlassian's grid carries no dates, so
  postings pass the age filter and get `posting_stale` flagged ("freshness unknown")
  rather than rejected
- **Crawl budget on very large boards** — 50-listing extraction cap per page; 8-page
  crawl bound
- **Run throughput** — sequential per-listing scoring; a 2-employer crawl run takes
  ~10 minutes
- Stuck `running` Run rows from interrupted invocations (cosmetic; `finalize_run`
  only runs when the graph completes)

## Deviations from plan

- The 02-VALIDATION.md "23 pytest commands" grep-count check counts 29 lines (the
  Test Infrastructure and Sampling sections legitimately reference pytest too); the
  23 map rows themselves are all green
- Dockerfile/compose grep counts differ by the strings appearing in the anti-race
  header comment the plan asked to preserve; semantic requirements verified live

*Phase: 02-headless-discovery-pipeline-cli-mvp-checkpoint*
*Completed: 2026-09-11*
