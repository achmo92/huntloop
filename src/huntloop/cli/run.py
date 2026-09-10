import sys
from huntloop.cli.main import EXIT_OK, EXIT_PARTIAL, EXIT_ABORTED
from huntloop.cli.render import render_run_human, render_run_json
from huntloop.db.base import get_engine, make_session_factory
from huntloop.graph.build import run_discovery
from huntloop.db.models import RunTrigger

def _make_session_factory():
    engine = get_engine()
    return make_session_factory(engine)

def cmd_run(args) -> int:
    sessionmaker = _make_session_factory()
    try:
        summary = run_discovery(
            sessionmaker=sessionmaker,
            no_score=args.no_score,
            trigger=RunTrigger.MANUAL,
            concurrency=args.concurrency,
        )
        if summary.top_listings and args.limit:
            summary.top_listings = summary.top_listings[:args.limit]
            
        print(render_run_json(summary) if args.json else render_run_human(summary))
        return EXIT_PARTIAL if summary.errors else EXIT_OK
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ABORTED
