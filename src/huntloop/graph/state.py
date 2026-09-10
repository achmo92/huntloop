import operator
from typing import Annotated, TypedDict


class EmployerResult(TypedDict, total=False):
    company_id: str
    company_name: str
    path: str            # "ats" | "crawl" | "skipped"
    fetched: int
    after_dedup: int
    after_deterministic: int
    after_triage: int
    scored: int
    written: int
    updated: int
    failed: int
    tokens_in: int
    tokens_out: int
    error: str | None
    stage: str | None


class DiscoveryState(TypedDict, total=False):
    run_id: str
    criteria_version: int
    no_score: bool
    company_ids: list[str]
    # operator.add, not replacement. Send-dispatched branches run concurrently and each returns a partial state; without an append reducer the last branch to finish silently discards every other employer's result, and the run looks like it only had one employer.
    employer_results: Annotated[list[EmployerResult], operator.add]
    errors: Annotated[list[dict], operator.add]
