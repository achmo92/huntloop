from __future__ import annotations
import enum
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol, Callable
import httpx
from tenacity import Retrying, stop_after_attempt, wait_exponential_jitter, retry_if_result, retry_if_exception_type
import json

logger = logging.getLogger(__name__)

class FetchStatus(str, enum.Enum):
    """
    Only this value may increment `companies.consecutive_empty_runs` (REG-05). An ERROR must never touch that counter.
    """
    OK = "ok"        # HTTP 200, parsed cleanly, >= 1 listing
    EMPTY = "empty"  # HTTP 200, parsed cleanly, exactly 0 listings  <- ONLY value that may feed REG-05
    ERROR = "error"  # non-200 / timeout / connection error / parse failure

class ErrorKind(str, enum.Enum):
    HTTP_ERROR = "http_error"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    PARSE_ERROR = "parse_error"

@dataclass(frozen=True)
class RawListing:
    external_id: str | None
    url: str
    title: str
    location_raw: str | None = None
    description_html: str | None = None
    description_plain: str | None = None
    posted_at: datetime | None = None          # tz-aware UTC
    is_remote: bool | None = None
    workplace_type: str | None = None          # platform's own enum string, verbatim
    country_code: str | None = None            # ISO-3166 alpha-2 when the platform supplies it
    secondary_locations: list[str] = field(default_factory=list)
    comp_min: Decimal | None = None
    comp_max: Decimal | None = None
    comp_currency: str | None = None
    comp_period: str | None = None             # "annual"|"monthly"|"hourly"|None
    comp_raw: str | None = None
    raw: dict = field(default_factory=dict)    # untouched source payload

@dataclass(frozen=True)
class FetchResult:
    status: FetchStatus
    listings: list[RawListing] = field(default_factory=list)
    http_status: int | None = None
    error_kind: ErrorKind | None = None
    message: str | None = None

    @property
    def is_confirmed_empty(self) -> bool:
        return self.status is FetchStatus.EMPTY

class AtsAdapter(Protocol):
    platform: str            # matches AtsPlatform value: "greenhouse" | "lever" | "ashby"
    def board_url(self, slug: str) -> str: ...
    def fetch(self, slug: str, *, client: httpx.Client | None = None) -> FetchResult: ...


DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
DEFAULT_HEADERS = {"User-Agent": "HuntLoop/0.1 (+self-hosted job discovery)"}
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(timeout=DEFAULT_TIMEOUT, headers=DEFAULT_HEADERS,
                        transport=transport, follow_redirects=True)

def _is_retryable_response(res) -> bool:
    if isinstance(res, httpx.Response):
        return res.status_code in RETRYABLE_STATUS
    return False

def _retry_after_wait(retry_state):
    # Check if the last attempt resulted in a response with Retry-After
    res = retry_state.outcome.result() if not retry_state.outcome.failed else None
    if res is not None and hasattr(res, "headers") and "Retry-After" in res.headers:
        try:
            wait_s = int(res.headers["Retry-After"])
            return min(float(wait_s), 30.0)
        except ValueError:
            pass
    return wait_exponential_jitter(initial=1, max=10)(retry_state)

def fetch_json(url: str, *, client: httpx.Client) -> tuple[int | None, object | None, ErrorKind | None, str | None]:
    from tenacity import RetryError
    retryer = Retrying(
        stop=stop_after_attempt(3),
        wait=_retry_after_wait,
        retry=retry_if_result(_is_retryable_response) | retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError, httpx.TransportError)),
        reraise=True
    )
    
    try:
        res = retryer(client.get, url)
    except RetryError as exc:
        last = exc.last_attempt
        if last.failed:
            err = last.exception()
            if isinstance(err, httpx.TimeoutException):
                return None, None, ErrorKind.TIMEOUT, str(err)
            if isinstance(err, (httpx.ConnectError, httpx.TransportError)):
                return None, None, ErrorKind.CONNECTION_ERROR, str(err)
            return None, None, ErrorKind.HTTP_ERROR, str(err)
        res = last.result()
    except Exception as exc:
        if isinstance(exc, httpx.TimeoutException):
            return None, None, ErrorKind.TIMEOUT, str(exc)
        if isinstance(exc, (httpx.ConnectError, httpx.TransportError)):
            return None, None, ErrorKind.CONNECTION_ERROR, str(exc)
        return None, None, ErrorKind.HTTP_ERROR, str(exc)
        
    if res.status_code == 200:
        logger.debug(f"fetch_json 200 OK: {url}")
        try:
            payload = res.json()
            return 200, payload, None, None
        except (json.JSONDecodeError, ValueError) as exc:
            return 200, None, ErrorKind.PARSE_ERROR, str(exc)
    
    if res.status_code == 429:
        return 429, None, ErrorKind.RATE_LIMITED, f"Rate limited: 429"
        
    return res.status_code, None, ErrorKind.HTTP_ERROR, f"HTTP {res.status_code}"


def classify_response(http_status: int | None, payload: object | None, parser: Callable[[object], list[RawListing]]) -> FetchResult:
    if payload is None:
        return FetchResult(status=FetchStatus.ERROR, http_status=http_status)
    try:
        listings = parser(payload)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return FetchResult(status=FetchStatus.ERROR, http_status=http_status,
                           error_kind=ErrorKind.PARSE_ERROR, message=str(exc))
    if not listings:
        return FetchResult(status=FetchStatus.EMPTY, http_status=http_status)
    return FetchResult(status=FetchStatus.OK, listings=listings, http_status=http_status)


def has_usable_description(listing: RawListing) -> bool:
    """DISC-02: a listing with no description text is unscoreable, not merely thin."""
    text = (listing.description_plain or listing.description_html or "").strip()
    return len(text) > 0
