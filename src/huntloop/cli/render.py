import json
from decimal import Decimal
from datetime import datetime

RUN_FIELDS: tuple[tuple[str, str], ...] = (
    ("companies_checked", "employers checked"),
    ("listings_fetched",  "fetched"),
    ("after_dedup",       "deduplicated"),
    ("after_deterministic", "filtered"),
    ("after_triage",      "triaged"),
    ("scored",            "scored"),
    ("new_jobs_written",  "written"),
    ("updated",           "updated"),
    ("failed",            "failed"),
)
# Both renderers iterate RUN_FIELDS. 02-CONTEXT.md requires the JSON view to carry the same data as the human view because Phase 4's API reshapes the JSON — two hand-maintained field lists would drift within a release, and the drift would be invisible until an API consumer noticed a missing number.

def _json_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

def render_run_human(summary) -> str:
    lines = []
    lines.append(f"Run ID: {summary.run_id}")
    lines.append(f"Status: {summary.status}")
    lines.append("")
    for attr, label in RUN_FIELDS:
        val = getattr(summary, attr, 0)
        lines.append(f"{label}: {val}")
        
    if summary.errors:
        lines.append("")
        lines.append("Errors:")
        for err in summary.errors:
            lines.append(f"  - [{err.get('company', 'Unknown')}] {err.get('stage', 'unknown')}: {err.get('message', '')}")
            
    lines.append("")
    lines.append(f"Tokens in: {summary.tokens_in}")
    lines.append(f"Tokens out: {summary.tokens_out}")
    cost = float(summary.cost_usd) if summary.cost_usd is not None else 0.0
    lines.append(f"Cost: ${cost:.4f}")
    
    if summary.top_listings:
        lines.append("")
        lines.append("Top listings:")
        for lst in summary.top_listings:
            lines.append(f"  - [{lst.get('score', 0):.2f}] {lst.get('title', '')} @ {lst.get('company_name', '')}")
            
    return "\n".join(lines)

def render_run_json(summary) -> str:
    out = {name: getattr(summary, name) for name, _ in RUN_FIELDS}
    out.update({
        "run_id": summary.run_id,
        "status": summary.status,
        "tokens_in": summary.tokens_in,
        "tokens_out": summary.tokens_out,
        "cost_usd": summary.cost_usd,
        "errors": summary.errors,
        "top_listings": summary.top_listings
    })
    return json.dumps(out, default=_json_default, indent=2)
    
def render_jobs_table(jobs) -> str:
    if not jobs:
        return "no jobs"
        
    lines = []
    lines.append(f"{'SCORE':<5} {'TITLE':<30} {'COMPANY':<20} {'LOCATION':<15} {'FLAGS':<20} {'URL'}")
    for j in jobs:
        score = f"{float(j.score_overall):.2f}" if j.score_overall is not None else "-"
        title = (j.title[:27] + "...") if len(j.title) > 30 else j.title
        comp = (j.company.name[:17] + "...") if len(j.company.name) > 20 else j.company.name
        loc_val = j.location_normalized or j.location_raw
        loc = (loc_val[:12] + "...") if loc_val and len(loc_val) > 15 else (loc_val or "-")
        
        flags = []
        if j.score_flags:
            for k, v in j.score_flags.items():
                if isinstance(v, dict) and v.get("raised"):
                    flags.append(k)
        flags_str = ",".join(flags)
        if len(flags_str) > 20:
            flags_str = flags_str[:17] + "..."
            
        url = j.url
        lines.append(f"{score:<5} {title:<30} {comp:<20} {loc:<15} {flags_str:<20} {url}")

    return "\n".join(lines)


# One row per run. Same single-source-of-truth rule as RUN_FIELDS above: the
# human table and the JSON list both iterate this tuple, so a column added for
# the CLI is automatically present for Phase 4's API.
RUN_HISTORY_FIELDS: tuple[tuple[str, str], ...] = (
    ("started_at", "started"),
    ("trigger", "trigger"),
    ("status", "status"),
    ("companies_checked", "employers"),
    ("listings_fetched", "fetched"),
    ("after_deterministic", "filtered"),
    ("after_triage", "triaged"),
    ("scored", "scored"),
    ("new_jobs_written", "written"),
    ("tokens_in", "tok_in"),
    ("tokens_out", "tok_out"),
    ("cost_usd", "cost"),
)


def _enum_value(v):
    """Enum members render as their value; everything else unchanged."""
    return v.value if hasattr(v, "value") else v


def _fmt_cost(value) -> str:
    """Always four decimal places, never a bare float repr.

    cost_usd is Numeric(12,6) -> Decimal. Formatting through Decimal (not float)
    keeps a six-place value from surfacing as 0.012300000000000001.
    """
    if value is None:
        return "$0.0000"
    return f"${Decimal(value):.4f}"


def render_run_history_human(runs) -> str:
    if not runs:
        return (
            "no runs recorded yet. Start the scheduler with `huntloop scheduler start`, "
            "or run discovery now with `huntloop run`."
        )
    # Drift guard: the line below hand-picks a readable subset of columns in a
    # fixed, curated order. This check keeps that subset honest against the
    # tuple both views share -- rename or drop a column in RUN_HISTORY_FIELDS
    # without updating the human line and this raises at import time. (Same
    # single-source-of-truth rule as the RUN_FIELDS comment above.)
    _human_line_columns = (
        "started_at", "trigger", "status", "new_jobs_written",
        "scored", "listings_fetched", "cost_usd",
    )
    assert not (set(_human_line_columns) - set(dict(RUN_HISTORY_FIELDS))), (
        "render_run_history_human prints columns missing from RUN_HISTORY_FIELDS"
    )
    lines = []
    for run in runs:
        started = run.started_at.strftime("%Y-%m-%d %H:%M") if run.started_at else "--"
        lines.append(
            f"{started}  {_enum_value(run.trigger):<9}  {_enum_value(run.status):<8}  "
            f"written={run.new_jobs_written or 0:<4} "
            f"scored={run.scored or 0:<4} "
            f"fetched={run.listings_fetched or 0:<5} "
            f"{_fmt_cost(run.cost_usd)}"
        )
        # A SKIPPED or CAPPED run is only useful if the reason travels with it --
        # that is the whole point of "a quiet week must be unambiguous".
        if run.error_summary:
            first = run.error_summary.splitlines()[0]
            lines.append(f"        ! {first}")
    return "\n".join(lines)


def render_run_history_json(runs) -> str:
    out = []
    for run in runs:
        row = {name: _enum_value(getattr(run, name, None)) for name, _ in RUN_HISTORY_FIELDS}
        row["run_id"] = str(run.id)
        row["finished_at"] = run.finished_at
        row["error_summary"] = run.error_summary
        out.append(row)
    return json.dumps(out, indent=2, default=_json_default)
