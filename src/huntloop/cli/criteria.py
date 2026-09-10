import sys
from pathlib import Path
from pydantic import ValidationError

from huntloop.cli.main import EXIT_OK, EXIT_PARTIAL, EXIT_ABORTED
from huntloop.db.base import get_engine, make_session_factory
from huntloop.criteria.loader import load_criteria_yaml, save_new_criteria_version
from huntloop.scoring.aggregate import recompute_backlog_overall_scores
from huntloop.db.models import CriteriaSource

def _make_session():
    engine = get_engine()
    return make_session_factory(engine)()

def cmd_load(args) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return EXIT_ABORTED
        
    try:
        payload = load_criteria_yaml(path)
    except ValidationError as e:
        print(f"error validation failed: {e}", file=sys.stderr)
        return EXIT_ABORTED
    except Exception as e:
        print(f"error loading yaml: {e}", file=sys.stderr)
        return EXIT_ABORTED
        
    session = _make_session()
    try:
        version = save_new_criteria_version(
            session, 
            payload, 
            source=CriteriaSource.MANUAL_EDIT
        )
        session.commit()
        print(f"criteria loaded: version {version}")
        
        if args.recompute:
            count = recompute_backlog_overall_scores(session, payload.dimension_weights.model_dump())
            session.commit()
            print(f"Recomputed backlog overall scores for {count} jobs")
            
        return EXIT_OK
    finally:
        session.close()
