"""RUN-01. `huntloop scheduler start` -- the long-running daemon entrypoint."""

import logging
import sys

from huntloop.cli.main import EXIT_ABORTED, EXIT_OK
from huntloop.config import load_config
from huntloop.scheduler.build import JOB_ID, build_scheduler


def cmd_scheduler_start(args) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        cfg = load_config()
        scheduler = build_scheduler(cfg)
        job = scheduler.get_job(JOB_ID)  # None before start(); informational only
        print(
            f"huntloop scheduler: daily discovery at {cfg.run_at} {cfg.timezone}"
            f"{'' if job is None else f' (next: {job.next_run_time})'}",
            flush=True,
        )
        scheduler.start()  # BlockingScheduler.start() blocks until SIGINT/SIGTERM
        return EXIT_OK
    except (KeyboardInterrupt, SystemExit):
        print("scheduler stopped", file=sys.stderr)
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ABORTED
