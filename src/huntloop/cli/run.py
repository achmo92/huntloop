import sys
from dataclasses import replace
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
            # RunSummary is frozen -- replace, never mutate (found live at the
            # 02-12 checkpoint: --limit defaults to 10, so every run crashed).
            summary = replace(summary, top_listings=tuple(summary.top_listings[:args.limit]))

        print(render_run_json(summary) if args.json else render_run_human(summary))
        return EXIT_PARTIAL if summary.errors else EXIT_OK
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ABORTED


def cmd_run_history(args) -> int:
    """RUN-05/RUN-07: show recent runs without needing SQL."""
    from huntloop.cli.render import render_run_history_human, render_run_history_json
    from huntloop.db.repository import RunRepository

    session = _make_session_factory()()
    try:
        runs = RunRepository(session).list_recent(limit=args.limit)
        out = render_run_history_json(runs) if args.json else render_run_history_human(runs)
        print(out)
    finally:
        session.close()
    # An empty history is a valid answer, not an error: exit 0 so `huntloop run
    # history | ...` in a shell pipeline behaves.
    return EXIT_OK
