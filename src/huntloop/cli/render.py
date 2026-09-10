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
