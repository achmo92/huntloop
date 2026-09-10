---
plan: 02-06
phase: 02-headless-discovery-pipeline-cli-mvp-checkpoint
status: complete
completed: 2026-09-10
requirements: [REG-01, REG-02, REG-03]
---

# 02-06 SUMMARY — Employer Resolution

## What was built

Four new modules implementing three-tier employer resolution:

### `src/huntloop/discovery/fetch/page.py`
- `MAX_HTML_BYTES = 5 * 1024 * 1024` (5 MiB body cap)
- `USER_AGENT = "HuntLoop/0.2 (+https://github.com/huntloop; personal job discovery)"`
- `RendererUnavailable(RuntimeError)` — lazy Playwright import failure
- `PageResult(frozen dataclass)` — `ok`, `url`, `final_url`, `status_code`, `html`, `rendered`, `truncated`, `error`
- `PageFetcher(Protocol)` — structural interface
- `StaticPageFetcher` — httpx with injected client (enables MockTransport testing)
- `RenderedPageFetcher` — lazy Playwright import, timeout → PageResult not raise

### `src/huntloop/registry/signatures.py`
- `SlugCandidate(frozen dataclass)` — `platform`, `slug`, `weak`, `matched_patterns`, `source`
- `SIGNATURES` — per-platform `(regex, label)` tuples, live-verified
- `WEAK_SIGNATURES` — `{"greenhouse": (r"gh_jid=",)}`
- `SLUG_BLOCKLIST = {"embed", "js", "jobs", "board", "api", "v1", "static", "assets"}`
- `sweep_signatures(html, *, source)` — case-insensitive, deduplicating, sorted (strong before weak)

### `src/huntloop/registry/probe.py`
- `SUFFIXES` — legal suffix tuple for `guess_slugs`
- `guess_slugs(name)` — 5 deterministic variants, capped, no empties
- `ProbeResult(frozen dataclass)` — `platform`, `slug`, `verified`, `job_count`, `reason`
- `probe_slug(platform, slug, *, client)` — one HTTP request; empty board = verified

### `src/huntloop/registry/resolve.py`
- `ResolutionStatus` — `RESOLVED`, `AMBIGUOUS`, `UNRESOLVED`, `NEEDS_REVERIFICATION`
- `ResolutionResult(frozen dataclass)` — full candidate/probe trail
- `resolve_employer(*, name, careers_url, client, static_fetcher, rendered_fetcher, max_guesses)`
- `build_ats_config(result)` — JSON-serialisable dict for `companies.ats_config`
- `persist_resolution(session, name, result, **extra)` — uses `CompanyRepository.upsert_by_name`

---

## AtsPlatform enum members used

```python
AtsPlatform.GREENHOUSE  # value = "greenhouse"
AtsPlatform.LEVER       # value = "lever"
AtsPlatform.ASHBY       # value = "ashby"
# Mapped in persist_resolution via AtsPlatform(result.platform)
```

---

## Final `ats_config["resolution"]` JSON shape (02-11 company list and Phase 4 REG-04 read this)

```json
{
  "resolution": {
    "status": "resolved",
    "resolved_via": "static_html",
    "careers_url": "https://cobalt.io/careers",
    "final_url": "https://cobalt.io/careers",
    "candidates": [
      {
        "platform": "greenhouse",
        "slug": "cobaltio",
        "source": "static_html",
        "weak": false,
        "matched_patterns": ["hosted_board"]
      }
    ],
    "probed": [
      {
        "platform": "greenhouse",
        "slug": "cobaltio",
        "verified": true,
        "job_count": 5,
        "reason": ""
      }
    ],
    "checked_at": "2026-09-10T08:10:00+00:00",
    "reason": ""
  }
}
```

For `UNRESOLVED`: `status = "unresolved"`, `reason` summarises tiers ran + probe count.
For `AMBIGUOUS`: `status = "ambiguous"`, all verified `(platform, slug)` pairs in `probed`.

---

## Capability reduction: name-only path without Firecrawl

02-CONTEXT.md rules Firecrawl out (both cloud and self-hosted).  Without
Firecrawl's `search()`, a bare-name registration cannot perform a web search
for the employer's ATS identity.  The **name-only path resolves via Tier 1
only**: generate slug guesses from the name and probe all three ATS APIs.

**Known failure mode**: NewRocket's ATS slug is `highmetric` — no trading-name
variant in `guess_slugs` will produce it.  Without a careers URL, NewRocket
would be registered as `UNRESOLVED`.  The resolution trail records
`resolved_via: null` so Phase 4's REG-04 review surface can identify employers
needing a URL update.  This is a stated, documented capability limitation, not
a bug.

---

## `COMPANY_DISCOVERY_OWNED_COLUMNS` — no extension needed

All columns written by `persist_resolution` (`ats`, `ats_identifier`, `ats_config`,
`careers_url`, `resolved_at`) were already present in `COMPANY_DISCOVERY_OWNED_COLUMNS`
in `src/huntloop/db/repository.py`.  No change needed.

---

## Deviations from plan

- **Ramp fixture pattern**: The `ramp_careers.html` fixture contains `ashbyhq.com/ramp/jobs` (bare domain, no `jobs.` subdomain), not a standard `jobs.ashbyhq.com/ramp` link.  An additional Ashby signature pattern `(r"ashbyhq\.com/([\w-]+)/jobs", "hosted_board_bare")` was added to detect this variant.

---

## Test results

| Selector | Tests | Status |
|----------|-------|--------|
| `-k PageFetcher` | 10 | ✅ |
| `-k "Signatures or Probe"` | 26 | ✅ |
| `-k add_employer` | 4 | ✅ (REG-01) |
| `-k slug_differs` | 2 | ✅ (REG-03) |
| `tests/registry/test_resolution.py -x` | 53 | ✅ |
| `tests/ -m "not postgres"` | 222 | ✅ |

## Self-Check: PASSED

- [x] All three tasks executed and committed
- [x] `COMPANY_DISCOVERY_OWNED_COLUMNS` verified — no extension needed
- [x] No Firecrawl dependency added anywhere
- [x] No `async def` in any new module
- [x] Playwright lazily imported; `RendererUnavailable` degrades to `UNRESOLVED`
- [x] `test_pattern_match_alone_never_resolves` present
- [x] Full candidate + probe trail persisted in `ats_config`
- [x] `pytest tests/ -q -m "not postgres"` exits 0 (222 passed)
