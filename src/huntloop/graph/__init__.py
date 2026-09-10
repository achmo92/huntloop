"""LangGraph orchestration package (02-10).

This package assembles the discovery pipeline as a StateGraph:
- load_employers: fetch enabled companies
- process_employer: fan-out via Send (one per employer)
- finalize_run: aggregate results and finish the run
"""

from huntloop.graph.state import DiscoveryState, EmployerResult
from huntloop.graph.build import build_graph, run_discovery, RunSummary

__all__ = [
    "DiscoveryState",
    "EmployerResult",
    "build_graph",
    "run_discovery",
    "RunSummary",
]
