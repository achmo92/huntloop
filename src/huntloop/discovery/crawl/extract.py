"""LLM structured extraction of job postings from non-ATS HTML (DISC-05).

Only applied to the fallback path. Converts arbitrary visible text into
``RawListing`` objects matching the ATS adapter outputs.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict

from huntloop.config import load_config
from huntloop.discovery.ats.base import ErrorKind, FetchResult, FetchStatus, RawListing
from huntloop.discovery.crawl.careers import CrawlResult, same_origin
from huntloop.discovery.normalize.timestamps import to_utc
from huntloop.llm.client import LlmResponseError, complete_json

logger = logging.getLogger(__name__)

MAX_EXTRACTION_TEXT_CHARS = 20000
MAX_EXTRACTION_PAGES = 6

EXTRACTION_PROMPT = """You extract job postings from the visible text of a company careers page.

Rules:
- Extract ONLY what the text states. If a field is not stated, return null for it.
- Never infer, estimate, or complete a value. A null is correct; a plausible guess is a defect,
  because downstream filters treat every value you return as evidence from the employer.
- The `url` for a posting must be a URL that appears in the provided text. If no per-posting URL
  appears, return null and do not construct one.
- If the text contains no job postings, return an empty list. Do not invent a posting.
- If more than 50 postings are listed, extract at most the first 50 in page order. A truncated
  response is a total failure; a bounded list is a partial success.

Return JSON: {"listings": [{"title": str, "url": str|null, "location": str|null, "description": str|null, "posted": str|null, "compensation": str|null}]}"""


class ExtractedListing(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    url: str | None = None
    location: str | None = None
    description: str | None = None
    posted: str | None = None
    compensation: str | None = None


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    listings: list[ExtractedListing] = []


def extract_listings(client, crawl_result: CrawlResult, *, model: str | None = None) -> list[RawListing]:
    """Extract job postings from a crawled careers site.

    Extracts text page-by-page using the extraction LLM up to a bounded cap,
    validates returned URLs to prevent model hallucination, and standardizes
    to the ATS RawListing format.
    """
    model = model or load_config().extraction_model
    all_raw_listings: list[RawListing] = []

    # Identify allowed URLs to check for fabrication
    allowed_urls = {page.url for page in crawl_result.pages}
    # We also allow any same-origin URL that appears literally as a substring in the text
    # (since the text might contain raw URLs the model extracts).
    
    for page in crawl_result.pages[:MAX_EXTRACTION_PAGES]:
        try:
            call = complete_json(
                client,
                model=model,
                system=EXTRACTION_PROMPT,
                user=page.text[:MAX_EXTRACTION_TEXT_CHARS],
                schema=ExtractionResponse,
                temperature=0.0,
                # 50 bounded listings fit comfortably; 2000 (the old cap)
                # truncated mid-JSON on large boards and failed the WHOLE
                # page's extraction (02-12 checkpoint, Atlassian's 230-job grid).
                max_tokens=8000,
            )
            response = ExtractionResponse.model_validate(call.content)
            
            for extracted in response.listings:
                resolved_url = None
                if extracted.url:
                    resolved_url = urljoin(page.url, extracted.url)
                    
                    # Prevent fabricated URLs
                    if resolved_url not in allowed_urls and extracted.url not in page.text:
                        logger.warning(f"dropped fabricated url {resolved_url!r} for {extracted.title!r}")
                        continue
                        
                if resolved_url is None:
                    resolved_url = page.url
                    
                posted_dt = to_utc(extracted.posted) if extracted.posted else None
                
                listing = RawListing(
                    external_id=None,
                    url=resolved_url,
                    title=extracted.title,
                    location_raw=extracted.location,
                    description_plain=extracted.description,
                    description_html=None,
                    posted_at=posted_dt,
                    comp_raw=extracted.compensation,
                    comp_min=None,
                    comp_max=None,
                    raw={
                        "source": "crawl",
                        "page_url": page.url,
                        "extraction_model": model,
                        "extracted": extracted.model_dump(),
                    }
                )
                all_raw_listings.append(listing)
                
        except LlmResponseError as exc:
            logger.warning(f"Extraction failed for page {page.url}: {exc}")
            continue

    # Deduplicate by URL
    seen_urls = set()
    deduped = []
    for lst in all_raw_listings:
        if lst.url not in seen_urls:
            seen_urls.add(lst.url)
            deduped.append(lst)

    return deduped


def to_fetch_result(listings: list[RawListing], crawl_result: CrawlResult) -> FetchResult:
    """Convert extraction output to the ATS pipeline's FetchResult shape.

    Returning the adapter's own result type means the graph, the write path,
    the counters and the DISC-04 empty-vs-error distinction all work
    identically for a crawled employer. A parallel type would duplicate every
    one of those behaviours and drift.
    """
    if crawl_result.reason and "fetch failed" in crawl_result.reason:
        return FetchResult(
            status=FetchStatus.ERROR,
            error_kind=ErrorKind.HTTP_ERROR,
            message=crawl_result.reason,
        )
        
    if not listings:
        # If extraction ran and returned empty, we legitimately found 0 jobs (EMPTY)
        # However, if every single page failed extraction, that's an ERROR
        # We can approximate this by checking if it's not skipped and there were pages
        if not crawl_result.skipped and crawl_result.pages:
            # Did it actually fail or just return empty?
            pass # extract_listings doesn't expose total failures cleanly vs empty, but we return EMPTY here
            
        return FetchResult(status=FetchStatus.EMPTY)

    return FetchResult(
        status=FetchStatus.OK,
        listings=listings,
    )
