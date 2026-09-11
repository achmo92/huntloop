"""RUN-01. `huntloop scheduler start` -- the long-running daemon entrypoint."""

import logging
import sys
import threading

from huntloop.cli.main import EXIT_ABORTED, EXIT_OK
from huntloop.config import load_effective_config
from huntloop.db.base import get_engine, make_session_factory
from huntloop.scheduler.build import JOB_ID, build_scheduler, watch_settings_and_reschedule


def cmd_scheduler_start(args) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        # Boot from the overlay too: a schedule saved in the UI before this
        # process started is honoured on the first build, not only by the
        # watcher's first poll.
        sessionmaker = make_session_factory(get_engine())
        session = sessionmaker()
        try:
            cfg = load_effective_config(session)
        finally:
            session.close()

        scheduler = build_scheduler(cfg)

        # The watcher follows later Setting changes (D-15). Started before the
        # blocking scheduler so a UI change reschedules the running job without
        # a restart. Daemon thread: it dies with the daemon, never blocks exit.
        watcher_stop = threading.Event()
        watcher = threading.Thread(
            target=watch_settings_and_reschedule,
            args=(scheduler, sessionmaker),
            kwargs={"stop": watcher_stop},
            name="huntloop-settings-watcher",
            daemon=True,
        )
        watcher.start()

        job = scheduler.get_job(JOB_ID)  # None before start(); informational only
        print(
            f"huntloop scheduler: daily discovery at {cfg.run_at} {cfg.timezone}"
            f"{'' if job is None else f' (next: {job.next_run_time})'}",
            flush=True,
        )
        try:
            scheduler.start()  # blocks until SIGINT/SIGTERM
        except (KeyboardInterrupt, SystemExit):
            print("scheduler stopped", file=sys.stderr)
        finally:
            # Stop the watcher in lockstep with the daemon: no thread outlives
            # the scheduler it was watching.
            watcher_stop.set()
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ABORTED
