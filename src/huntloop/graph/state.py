"""LangGraph state definitions (02-10).

State is a TypedDict, NOT a Pydantic model. LangGraph 1.x expects a TypedDict
for StateGraph, and the reducers are Annotated[list[...], operator.add].

operator.add, not replacement. Send-dispatched branches run concurrently and each
returns a partial state; without an append reducer the last branch to finish
silently discards every other employer's result, and the run looks like it only
had one employer.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class EmployerResult(TypedDict, total=False):
    """Result from processing a single employer.
    
    All fields are optional (total=False) because partial results are valid.
    An employer that fails early still produces a result with what it completed.
    """
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
    # Distinct from `error`: a cap is a deliberate budget stop, not a failure,
    # and must not push run_status() to PARTIAL.
    capped: bool         # True when the run-level spend cap stopped this employer's scoring


class DiscoveryState(TypedDict, total=False):
    """State for the discovery graph.
    
    employer_results and errors use operator.add reducers so concurrent branches
    all contribute rather than one overwriting the other.
    """
    run_id: str
    criteria_version: int
    no_score: bool
    company_ids: list[str]
    employer_results: Annotated[list[EmployerResult], operator.add]
    errors: Annotated[list[dict], operator.add]
