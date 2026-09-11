# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React 19 + TypeScript + Vite + Tailwind CSS 4 + shadcn/ui (scaffolded in `web/`), FastAPI backend served same-origin under `/api`. [Inferred from the phase plan and STACK.md research; the plan-of-record pins these.]

## Users

A single non-technical person — a job seeker conducting their own search — checking a daily job-pipeline dashboard on their own private network. They describe what they want once, then monitor: employers registering, listings surfacing, statuses changing. They never edit files or use a terminal. [Confirmed in PROJECT.md "User model" constraint and the phase context.]

## Product Purpose

HuntLoop watches employers' job boards on a schedule, filters and scores what it finds against the user's stated criteria, tracks the resulting pipeline, and adapts its criteria from what happens to the roles it surfaced. Success means relevant roles appear without anyone going looking — and "relevant" improves on its own. [Confirmed: PROJECT.md Core Value.]

## Positioning

Self-hosted and private: search activity, compensation expectations, and pipeline data never leave the user's instance. It covers continuous discovery before a URL is in hand — the adjacent stretch existing interactive co-pilot tools (fit/CV/outreach for a known URL) don't serve. [Confirmed: PROJECT.md Context + Privacy constraint.]

## Operating Context

Daily check-ins: open the dashboard, see pipeline counts by stage, next scheduled run, last run outcome with cost; act on scored listings; occasionally tune criteria, employers, settings. Runs happen unattended on a schedule. Quiet weeks must be explained on the dashboard, not mistaken for an empty market. [Inferred from phase context D-14/D-17 and Phase 3 run-health requirements.]

## Capabilities and Constraints

- Criteria intake: describe-first freeform paragraph, then an editable structured form; drag-to-order dimension ranking; versioned saves with peekable history. [Confirmed: D-01..D-04.]
- Employer registry with automatic resolution, batch review of proposals, honest coverage disclosure ("we'd target ~40, we can watch 28"), per-employer retry. [Confirmed: D-05..D-08.]
- Listings workspace: dense sortable table + detail drawer, one-click status transitions (no confirmation dialog — this is the adaptive loop's only signal source), composable filters. [Confirmed: D-09..D-13.]
- Settings in the interface, never in files: API access, model per stage, schedule, spend cap — each section independently saveable. [Confirmed: D-15, PROJECT.md "configuration lives in the interface".]
- Private-network access, no auth code, no cross-origin requests. [Confirmed: PROJECT.md key decision.]
- Genericity: no assumptions about field, country, currency, or language. [Confirmed: PROJECT.md constraint.]
- Per-run spend caps and visible cost accounting are requirements, not niceties. [Confirmed: PROJECT.md Cost constraint.]

## Brand Commitments

Honesty is the brand: coverage ceilings stated up front, failures surfaced as states with next steps, empty states that tell the user what to do next — never blank tables. [Confirmed: PROJECT.md coverage-honesty principle and D-18.]

## Evidence on Hand

Live-verified ATS JSON endpoints (Greenhouse/Lever/Ashby, no credentials); a working headless pipeline (Phases 1–3) with 468 green tests, per-stage counts, and real USD cost accounting; the FastAPI criteria API (plan 04-01). No logos, imagery, or testimonials exist — do not fabricate any.

## Product Principles

- Relevant roles surface without anyone going looking.
- Quiet is explained, never assumed: a silent failure must be visible rather than mistaken for an empty market.
- One-click actions where friction would starve the feedback loop.
- The user's data stays on the user's machine.
- Empty states say what to do next; the interface never dead-ends.
- Dense but calm: this is a monitoring surface read daily, not a marketing page.

## Accessibility & Inclusion

Responsive web reachable on any device on the user's own network; no native apps. [Confirmed: PROJECT.md Out of Scope/UI-01.]
