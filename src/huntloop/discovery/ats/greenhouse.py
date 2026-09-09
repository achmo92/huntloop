from datetime import datetime, timezone
import html
from typing import Any
import httpx

from huntloop.discovery.ats.base import (
    AtsAdapter, FetchResult, RawListing, fetch_json, classify_response, make_client
)

class GreenhouseAdapter:
    platform = "greenhouse"

    def board_url(self, slug: str) -> str:
        return f"https://job-boards.greenhouse.io/{slug}"

    def fetch(self, slug: str, *, client: httpx.Client | None = None) -> FetchResult:
        if client is None:
            client = make_client()
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        http_status, payload, error_kind, msg = fetch_json(url, client=client)
        
        if error_kind is not None:
            from huntloop.discovery.ats.base import FetchStatus
            return FetchResult(status=FetchStatus.ERROR, http_status=http_status, error_kind=error_kind, message=msg)

        return classify_response(http_status, payload, self._parse)

    def _parse(self, payload: object) -> list[RawListing]:
        if not isinstance(payload, dict) or "jobs" not in payload or not isinstance(payload["jobs"], list):
            raise ValueError("Payload must be a dict containing a 'jobs' list")
        
        listings = []
        for job in payload["jobs"]:
            # Pitfall: Greenhouse's `content` field is double HTML-entity-encoded
            content_raw = job.get("content") or ""
            description_html = html.unescape(content_raw)
            
            location_raw = None
            loc_dict = job.get("location")
            if isinstance(loc_dict, dict):
                location_raw = loc_dict.get("name")
            
            posted_at = None
            date_str = job.get("updated_at") or job.get("first_published")
            if date_str:
                try:
                    posted_at = datetime.fromisoformat(date_str.replace('Z', '+00:00')).astimezone(timezone.utc)
                except ValueError:
                    pass
            
            listings.append(
                RawListing(
                    external_id=str(job.get("id")),
                    url=job.get("absolute_url", ""),
                    title=job.get("title", ""),
                    location_raw=location_raw,
                    posted_at=posted_at,
                    description_html=description_html,
                    description_plain=None,
                    raw=job,
                )
            )
        return listings

ADAPTER = GreenhouseAdapter()
