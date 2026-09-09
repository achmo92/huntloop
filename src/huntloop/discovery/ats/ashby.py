from datetime import datetime, timezone
from decimal import Decimal
import httpx

from huntloop.discovery.ats.base import (
    AtsAdapter, FetchResult, RawListing, fetch_json, classify_response, make_client
)

class AshbyAdapter:
    platform = "ashby"

    def board_url(self, slug: str) -> str:
        return f"https://jobs.ashbyhq.com/{slug}"

    def fetch(self, slug: str, *, client: httpx.Client | None = None) -> FetchResult:
        if client is None:
            client = make_client()
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
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
            country_code = None
            addr = job.get("address")
            if isinstance(addr, dict):
                postal = addr.get("postalAddress")
                if isinstance(postal, dict):
                    country_code = postal.get("addressCountry")
            
            secondary_locations = []
            for loc in (job.get("secondaryLocations") or []):
                if isinstance(loc, dict) and loc.get("location"):
                    secondary_locations.append(loc["location"])
            
            posted_at = None
            date_str = job.get("publishedAt")
            if date_str:
                try:
                    posted_at = datetime.fromisoformat(date_str.replace('Z', '+00:00')).astimezone(timezone.utc)
                except ValueError:
                    pass
            
            comp_min = None
            comp_max = None
            comp_currency = None
            comp_period = None
            comp_raw = None
            
            comp = job.get("compensation")
            if isinstance(comp, dict):
                tiers = comp.get("compensationTiers") or []
                if tiers and isinstance(tiers, list) and isinstance(tiers[0], dict):
                    tier = tiers[0]
                    if "minValue" in tier:
                        comp_min = Decimal(str(tier["minValue"]))
                    if "maxValue" in tier:
                        comp_max = Decimal(str(tier["maxValue"]))
                    comp_currency = tier.get("currencyCode")
                    interval = tier.get("interval")
                    if interval == "YEAR":
                        comp_period = "annual"
                    elif interval == "MONTH":
                        comp_period = "monthly"
                    elif interval == "HOUR":
                        comp_period = "hourly"
                    comp_raw = tier.get("tierSummary") or tier.get("title")

            listings.append(
                RawListing(
                    external_id=str(job.get("id")),
                    url=job.get("jobUrl", ""),
                    title=job.get("title", ""),
                    location_raw=job.get("location"),
                    is_remote=job.get("isRemote"),
                    workplace_type=job.get("workplaceType"),
                    country_code=country_code,
                    secondary_locations=secondary_locations,
                    description_html=job.get("descriptionHtml"),
                    description_plain=job.get("descriptionPlain"),
                    posted_at=posted_at,
                    comp_min=comp_min,
                    comp_max=comp_max,
                    comp_currency=comp_currency,
                    comp_period=comp_period,
                    comp_raw=comp_raw,
                    raw=job,
                )
            )
        return listings

ADAPTER = AshbyAdapter()
