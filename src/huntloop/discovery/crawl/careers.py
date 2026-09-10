"""Careers-page discovery and content hashing (DISC-05).

Non-ATS fallback path. Fetches pages within same-origin bounds, extracts
visible text, and computes a stable hash to skip unchanged pages on
subsequent runs without an expensive extraction call.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from huntloop.db.repository import CompanyRepository

CAREERS_PATH_HINTS: tuple[str, ...] = (
    "career", "careers", "jobs", "job", "join-us", "join_us", "openings",
    "opportunities", "vacancies", "work-with-us", "hiring", "positions", "roles",
)
DEFAULT_MAX_PAGES = 8          # One index page plus up to seven detail pages. Enough to see a
                               # small company's whole board; small enough that a runaway crawl
                               # cannot happen on a site with pagination.
SKIP_TAGS = ("script", "style", "noscript", "svg")


@dataclass(frozen=True)
class CrawledPage:
    url: str
    text: str
    hash: str


@dataclass(frozen=True)
class CrawlResult:
    base_url: str
    pages: tuple[CrawledPage, ...] = ()
    page_hash: str = ""            # hash of the base/index page, for the next run's skip check
    skipped: bool = False
    reason: str = ""
    rendered: bool = False


def visible_text(html: str) -> str:
    """Extract visible text from HTML.

    Strips specific tags, remaining HTML tags, unescapes entities, and collapses
    whitespace to a single space. Uses stdlib only.
    """
    if not html:
        return ""
    
    # Strip skip tags completely (e.g. <script>...</script>)
    # re.S allows . to match newlines, re.I is case insensitive.
    pattern = rf"<({'|'.join(SKIP_TAGS)})\b.*?</\1>"
    text = re.sub(pattern, " ", html, flags=re.S | re.I)
    
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    
    # Unescape HTML entities
    text = html_lib.unescape(text)
    
    # Collapse whitespace
    return " ".join(text.split())


def content_hash(html: str) -> str:
    """Hash the VISIBLE text, not the raw HTML, to power the skip logic.

    Hashing the VISIBLE text, not the raw HTML, is what makes the skip useful.
    Raw HTML changes on nearly every request — CSRF nonces, analytics session
    IDs, cache-busting asset hashes — so a raw hash would never match and the
    skip would silently never fire, costing a full extraction call on every run
    forever. That failure is invisible: everything still works, it just costs money.
    """
    text = visible_text(html)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def extract_links(html: str, base_url: str) -> list[str]:
    """Extract and absolute-ize links from HTML, dropping fragments and non-HTTP schemes."""
    if not html:
        return []
        
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I)
    
    seen = set()
    result = []
    
    for href in hrefs:
        # Resolve to absolute
        resolved = urljoin(base_url, href)
        
        # Drop fragments
        if "#" in resolved:
            resolved = resolved.split("#", 1)[0]
            
        # Parse and check scheme
        parsed = urlparse(resolved)
        if parsed.scheme not in ("http", "https"):
            continue
            
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
            
    return result


def same_origin(url: str, base_url: str) -> bool:
    """Check if the url is on the same registrable domain as the base_url.

    Suffix matching on the raw host ("host.endswith('acme.com')") is the classic
    bug here — it accepts acme.com.attacker.net. Compare labels, not string suffixes.
    """
    parsed_url = urlparse(url)
    parsed_base = urlparse(base_url)
    
    if not parsed_url.hostname or not parsed_base.hostname:
        return False
        
    url_labels = parsed_url.hostname.lower().split(".")
    base_labels = parsed_base.hostname.lower().split(".")
    
    if len(url_labels) < 2 or len(base_labels) < 2:
        return parsed_url.hostname == parsed_base.hostname
        
    # Compare the last two labels (registrable domain approximation)
    return url_labels[-2:] == base_labels[-2:]


def _is_priority_link(url: str) -> bool:
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    for hint in CAREERS_PATH_HINTS:
        if hint in path_lower:
            return True
    return False


def crawl_careers(
    fetcher,
    base_url: str,
    *,
    previous_hash: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    rendered_fetcher=None,
) -> CrawlResult:
    """Crawl a careers site, starting at base_url.

    Respects same-origin bounds, depth bounds (max_pages), and short-circuits
    if the base page hash matches the previous_hash.
    """
    # 1. Fetch base_url
    try:
        result = fetcher.fetch(base_url)
    except Exception as exc:
        return CrawlResult(base_url, reason=f"fetch exception: {exc}")
        
    if not result.ok:
        return CrawlResult(base_url, reason=f"fetch failed: {result.status_code or result.error}")

    # 2. Check hash
    html = result.html
    page_hash = content_hash(html)
    if previous_hash and page_hash == previous_hash:
        return CrawlResult(base_url, page_hash=page_hash, skipped=True, reason="unchanged")

    rendered = False
    # 3. If near-empty shell and rendered_fetcher provided, retry
    if len(visible_text(html)) < 400 and rendered_fetcher is not None:
        try:
            rendered_result = rendered_fetcher.fetch(base_url)
            if rendered_result.ok:
                html = rendered_result.html
                page_hash = content_hash(html)
                rendered = True
        except Exception as exc:
            # Catch RendererUnavailable (or any error) and ignore, sticking to static
            pass

    # 4. Collect links and crawl detail pages
    all_links = extract_links(html, base_url)
    same_origin_links = [link for link in all_links if same_origin(link, base_url)]
    
    # Sort: priority links first
    same_origin_links.sort(key=lambda url: 0 if _is_priority_link(url) else 1)
    
    visited = {base_url}
    pages = [CrawledPage(url=base_url, text=visible_text(html), hash=page_hash)]
    
    for link in same_origin_links:
        if len(pages) >= max_pages:
            break
        if link in visited:
            continue
            
        visited.add(link)
        try:
            link_fetcher = rendered_fetcher if rendered else fetcher
            if link_fetcher is None:
                link_fetcher = fetcher
                
            link_res = link_fetcher.fetch(link)
            if link_res.ok:
                link_html = link_res.html
                link_text = visible_text(link_html)
                pages.append(
                    CrawledPage(
                        url=link, 
                        text=link_text, 
                        hash=content_hash(link_html)
                    )
                )
        except Exception:
            pass

    return CrawlResult(
        base_url=base_url,
        pages=tuple(pages),
        page_hash=page_hash,
        rendered=rendered,
    )


def save_crawl_hash(session, company, page_hash: str, *, now: datetime | None = None) -> None:
    """Save the page_hash to companies.ats_config.

    Read-modify-write the whole ats_config dict. 02-06 stores the full resolution
    trail under the "resolution" key of this same column; replacing the dict instead
    of merging would silently discard the candidate/probe history that makes a stale
    slug diagnosable. And copying rather than mutating in place matters because
    SQLAlchemy does not track in-place mutation of a plain JSON dict.
    """
    config = dict(company.ats_config or {})
    # never mutate the ORM dict in place, SQLAlchemy will not detect an in-place edit
    config["crawl"] = {
        "page_hash": page_hash,
        "checked_at": (now or datetime.now(timezone.utc)).isoformat()
    }
    CompanyRepository(session).upsert_by_name(company.name, ats_config=config)


def load_crawl_hash(company) -> str | None:
    """Read the page_hash from companies.ats_config."""
    return (company.ats_config or {}).get("crawl", {}).get("page_hash")
