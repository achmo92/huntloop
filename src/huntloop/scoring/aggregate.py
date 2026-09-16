from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select
from huntloop.db.models import Job
from huntloop.scoring.config import DIMENSIONS

# SCOR-11 assertion: This module structurally cannot make a model call.
# Do not import huntloop.llm or openai.

class UnscoreableError(ValueError):
    """No dimension carried both a score and a non-zero weight."""

def compute_overall_score(dimension_scores: dict[str, float | None],
                          weights: dict[str, float]) -> Decimal:
    """
    SCOR-08: an unassessable dimension is renormalised OUT of the divisor, never replaced with a neutral 3. 
    Substituting a neutral value quietly drags every score toward the middle and makes an unassessable 
    dimension indistinguishable from an average one. 
    SCOR-07: this arithmetic is the only source of `score_overall` — no model is ever asked for it.
    """
    missing = [d for d in dimension_scores if d not in weights]
    if missing:
        raise KeyError(f"No weight supplied for dimension(s): {missing}")
        
    assessed = {d: s for d, s in dimension_scores.items() if s is not None}

    total_weight = sum(Decimal(str(weights[d])) for d in assessed)
    
    if total_weight == 0:
        raise UnscoreableError(
            "no assessable dimensions: every scored dimension had zero weight, or "
            "every dimension came back null"
        )
        
    weighted = sum(Decimal(str(weights[d])) * Decimal(str(assessed[d])) for d in assessed)
    return (weighted / total_weight).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def recompute_backlog_overall_scores(session, weights: dict[str, float]) -> int:
    """
    SCOR-11. Reads only the STORED per-dimension scores. It does not import `huntloop.llm`, 
    construct a client, or issue any network call — the dimension scores are the durable 
    artifact and the overall score is a derived view. This is the whole reason the model 
    is not asked for an overall score.
    """
    rows = session.execute(
        select(Job).where(Job.score_dimensions.is_not(None))
    ).scalars().all()
    
    updated = 0
    for job in rows:
        scores = {}
        for d in DIMENSIONS:
            val = job.score_dimensions.get(d.name) if isinstance(job.score_dimensions, dict) else None
            if isinstance(val, dict):
                scores[d.name] = val.get("score")
            else:
                scores[d.name] = val
                
        try:
            job.score_overall = compute_overall_score(scores, weights)
        except UnscoreableError:
            continue          # all-null dimensions: leave the prior value untouched
        except KeyError:
            continue
            
        updated += 1
        
    session.flush()
    return updated
