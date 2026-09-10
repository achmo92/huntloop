"""
Run manually before closing the phase. Not part of the pytest suite: it depends on three companies' job boards being up and on their ATS URLs not having moved, neither of which is this project's code. Research observed these three boards on 2026-09-09 with a stated 30-day validity window; a failure here means the signature table needs re-checking, not that the adapters are broken.
"""

import sys
from huntloop.discovery.ats.registry import get_adapter
from huntloop.discovery.ats.base import FetchStatus

CASES = [
    ("greenhouse", "cobaltio"),
    ("lever", "spotify"),
    ("ashby", "ramp"),
]

def run():
    all_passed = True
    results = []

    for platform, slug in CASES:
        adapter = get_adapter(platform)
        result = adapter.fetch(slug)
        
        passed = True
        reason = ""
        desc_count = 0
        first_title = ""

        if result.status is not FetchStatus.OK:
            passed = False
            reason = f"Status not OK: {result.status}"
        elif len(result.listings) == 0:
            passed = False
            reason = "0 listings returned"
        else:
            first_title = result.listings[0].title
            desc_count = sum(1 for j in result.listings if j.description_html and j.description_html.strip())
            
            if desc_count == 0:
                passed = False
                reason = "No listings have descriptions"
            elif any(not j.external_id for j in result.listings):
                passed = False
                reason = "Missing external_id on some listings"
            elif any(not j.url or not j.url.startswith("http") for j in result.listings):
                passed = False
                reason = "Missing or invalid url on some listings"

        if passed:
            print(f"{platform} {slug} -> {len(result.listings)} listings, {desc_count} with descriptions, first: {first_title}")
            results.append((platform, slug, "PASS"))
        else:
            print(f"{platform} {slug} -> FAIL ({reason})")
            results.append((platform, slug, "FAIL"))
            all_passed = False
            
    print("\n--- Summary ---")
    for platform, slug, status in results:
        print(f"{platform:<15} {slug:<15} {status}")
        
    if not all_passed:
        sys.exit(1)

if __name__ == "__main__":
    run()
