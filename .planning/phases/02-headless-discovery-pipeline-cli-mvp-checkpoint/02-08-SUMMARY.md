---
plan: 02-08
phase: 02-headless-discovery-pipeline-cli-mvp-checkpoint
status: complete
completed: 2026-09-10
requirements: [DISC-05]
---

# 02-08 SUMMARY — Fallback Crawl & Extraction

## What was built

The fallback path for employers without a supported ATS. This path crawls an employer's careers site and uses the LLM to extract job postings directly from the visible text.

### `src/huntloop/discovery/crawl/careers.py`
- `crawl_careers` orchestration with bounds (max pages = 8).
- Resolves and fetches only same-origin links (verifying registrable domains, not just suffix matching).
- Prioritises paths containing keywords like "careers", "jobs", "roles".
- `content_hash` computes a stable SHA256 of strictly the *visible* text (omitting scripts, styles, raw HTML tags) to short-circuit the expensive extraction process when a careers page hasn't actually changed.

### `src/huntloop/discovery/crawl/extract.py`
- `extract_listings` uses the `Config.extraction_model` to parse visible text into ATS-compatible `RawListing` objects.
- Hallucination prevention: extracted URLs are checked against a verified set of same-origin links and page text. If a URL is hallucinated by the model, the listing is dropped entirely to prevent phantom dedup keys.
- Maps extraction failures gracefully and converts the final output into the same `FetchResult` shape used by the ATS adapters.

## Implementation Details & Contract Outputs

### Merged `ats_config` Shape
The content hash is persisted in `companies.ats_config` under a `crawl` key alongside the existing `resolution` data from 02-06. The read-modify-write strategy preserves the entire resolution trail:
```json
{
  "resolution": {
    "status": "resolved",
    "resolved_via": "static_html",
    ...
  },
  "crawl": {
    "page_hash": "a1b2c3d4e5f6g7h8",
    "checked_at": "2026-09-10T08:24:00+00:00"
  }
}
```

### Known Limitations (Dedup keys for deep-linkless postings)
If a careers page lists multiple roles on a single page without linking to distinct detail pages (deep links), the extraction model returns `null` for their URLs. The system falls back to assigning them the parent `page.url`. Because URL is the primary dedup key for crawled listings (which have `external_id=None`), this will collapse multiple distinct roles on the same page into a single row. This is a known and accepted limitation of the headless crawl path.

### Provenance Tracking
Every extracted listing carries its origin in the `raw` dict, ensuring its lower trust tier compared to an ATS API is trackable:
```python
raw={
    "source": "crawl",
    "page_url": "https://acme.com/careers",
    "extraction_model": "gpt-4o-mini",
    "extracted": {
        "title": "Software Engineer",
        "url": "/roles/swe",
        "location": "Remote",
        "description": "...",
        "posted": "2024-01-01",
        "compensation": "$150k"
    }
}
```

### Observed Cost Baseline (for 02-12)
By design, unchanged pages cost **0** tokens due to `content_hash` short-circuiting. When the page *does* change, a single extraction call consumes approximately 50-200 prompt tokens (depending on visible text length) and a few hundred completion tokens, constrained to `MAX_EXTRACTION_TEXT_CHARS = 20000` and `MAX_EXTRACTION_PAGES = 6`.

## Test Results

| Selector | Tests | Status |
|----------|-------|--------|
| `test_fallback_crawl.py` | 14 | ✅ |
| `test_openai_import_is_confined_to_llm_client` | 1 | ✅ |
| `tests/ -m "not postgres"` | 262 | ✅ |

## Self-Check: PASSED
- [x] Crawl logic bounded by same-origin rules and max_pages.
- [x] Unchanged text hashes exactly identical, saving model calls.
- [x] Extraction logic validates URLs and refuses to hallucinate data.
- [x] Same `FetchResult` and `RawListing` shapes returned as ATS path.
- [x] Persisted state merges cleanly with `ats_config`.
