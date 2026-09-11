import argparse
import sys

EXIT_OK = 0          # everything succeeded
EXIT_PARTIAL = 1     # the run completed but some employers failed
EXIT_ABORTED = 2     # the run could not complete at all
# Three-valued, per 02-CONTEXT.md. Phase 3's scheduler must distinguish a degraded run from a dead one without parsing text, and once scripts depend on "non-zero means broken" that distinction can never be added back.

from huntloop.cli.company import cmd_add, cmd_import, cmd_resolve, cmd_list
from huntloop.cli.criteria import cmd_load
from huntloop.cli.run import cmd_run
from huntloop.cli.jobs import cmd_jobs_list
from huntloop.config import ConfigError
from huntloop.graph.build import NoActiveCriteria

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="huntloop")
    parser.add_argument("--version", action="version", version="huntloop 0.1.0")
    
    subparsers = parser.add_subparsers(title="commands", dest="command")
    
    # company
    parser_company = subparsers.add_parser("company", help="Manage employers")
    company_sub = parser_company.add_subparsers(dest="company_command", required=True)
    
    # company add
    parser_add = company_sub.add_parser("add", help="Add an employer")
    parser_add.add_argument("name", help="Employer name")
    parser_add.add_argument("--url", help="Careers page URL")
    parser_add.add_argument("--no-render", action="store_true", help="Skip rendered tier")
    parser_add.add_argument("--disabled", action="store_true", help="Add as disabled")
    parser_add.set_defaults(func=cmd_add)
    
    # company import
    parser_import = company_sub.add_parser("import", help="Import employers from YAML")
    parser_import.add_argument("path", help="Path to YAML file")
    parser_import.add_argument("--no-render", action="store_true", help="Skip rendered tier")
    parser_import.set_defaults(func=cmd_import)
    
    # company resolve
    parser_resolve = company_sub.add_parser("resolve", help="Re-resolve an employer")
    parser_resolve.add_argument("name", help="Employer name")
    parser_resolve.add_argument("--no-render", action="store_true", help="Skip rendered tier")
    parser_resolve.set_defaults(func=cmd_resolve)
    
    # company list
    parser_list = company_sub.add_parser("list", help="List employers")
    parser_list.add_argument("--json", action="store_true", help="Output JSON")
    parser_list.set_defaults(func=cmd_list)
    
    # criteria
    parser_criteria = subparsers.add_parser("criteria", help="Manage criteria")
    criteria_sub = parser_criteria.add_subparsers(dest="criteria_command", required=True)
    
    # criteria load
    parser_load = criteria_sub.add_parser("load", help="Load criteria from YAML")
    parser_load.add_argument("path", help="Path to YAML file")
    parser_load.add_argument("--recompute", action="store_true", help="Recompute backlog scores")
    parser_load.set_defaults(func=cmd_load)
    
    # run
    parser_run = subparsers.add_parser("run", help="Run discovery pipeline")
    parser_run.add_argument("--no-score", action="store_true", help="Skip scoring (no model calls)")
    parser_run.add_argument("--json", action="store_true", help="Output JSON")
    parser_run.add_argument("--concurrency", type=int, help="Max employer concurrency")
    parser_run.add_argument("--limit", type=int, default=10, help="Max top listings to show")
    parser_run.set_defaults(func=cmd_run)
    
    # jobs
    parser_jobs = subparsers.add_parser("jobs", help="Manage jobs")
    jobs_sub = parser_jobs.add_subparsers(dest="jobs_command", required=True)
    
    # jobs list
    parser_jobs_list = jobs_sub.add_parser("list", help="List scored jobs")
    parser_jobs_list.add_argument("--min-score", type=float, help="Minimum overall score")
    parser_jobs_list.add_argument("--limit", type=int, default=25, help="Max jobs to list")
    parser_jobs_list.add_argument("--company", help="Filter by company name")
    parser_jobs_list.add_argument("--json", action="store_true", help="Output JSON")
    parser_jobs_list.add_argument("--flagged", action="store_true", help="Only show flagged jobs")
    parser_jobs_list.set_defaults(func=cmd_jobs_list)

    # scheduler
    from huntloop.cli.scheduler import cmd_scheduler_start

    parser_scheduler = subparsers.add_parser(
        "scheduler", help="Run discovery unattended on a schedule"
    )
    scheduler_sub = parser_scheduler.add_subparsers(dest="scheduler_command", required=True)
    parser_scheduler_start = scheduler_sub.add_parser(
        "start", help="Start the blocking daily scheduler (runs until stopped)"
    )
    parser_scheduler_start.set_defaults(func=cmd_scheduler_start)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_OK
        
    try:
        return args.func(args)
    except (ConfigError, NoActiveCriteria) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ABORTED
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
        return EXIT_ABORTED


if __name__ == "__main__":
    sys.exit(main())
