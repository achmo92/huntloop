from datetime import datetime, timezone
import httpx

from huntloop.discovery.ats.base import (
    AtsAdapter, FetchResult, RawListing, fetch_json, classify_response, make_client
)

class LeverAdapter:
    platform = "lever"

    def board_url(self, slug: str) -> str:
        return f"https://jobs.lever.co/{slug}"

    def fetch(self, slug: str, *, client: httpx.Client | None = None) -> FetchResult:
        if client is None:
            client = make_client()
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        http_status, payload, error_kind, msg = fetch_json(url, client=client)
        
        if error_kind is not None:
            from huntloop.discovery.ats.base import FetchStatus
            return FetchResult(status=FetchStatus.ERROR, http_status=http_status, error_kind=error_kind, message=msg)

        return classify_response(http_status, payload, self._parse)

    def _parse(self, payload: object) -> list[RawListing]:
        if not isinstance(payload, list):
            raise ValueError("Payload must be a top-level list")
        
        listings = []
        for posting in payload:
            is_remote = None
            wt = posting.get("workplaceType")
            if wt and wt.lower() == "remote":
                is_remote = True
            
            cats = posting.get("categories") or {}
            posted_at = datetime.fromtimestamp(posting["createdAt"]/1000, tz=timezone.utc)
            
            listings.append(
                RawListing(
                    external_id=str(posting.get("id")),
                    url=posting.get("hostedUrl", ""),
                    title=posting.get("text", ""),
                    location_raw=cats.get("location"),
                    secondary_locations=list(cats.get("allLocations") or []),
                    country_code=posting.get("country"),
                    workplace_type=wt,
                    is_remote=is_remote,
                    description_html=posting.get("description"),
                    description_plain=posting.get("descriptionPlain"),
                    posted_at=posted_at,
                    raw=posting,
                )
            )
        return listings

ADAPTER = LeverAdapter()
