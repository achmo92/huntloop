"""RUN-03 / RUN-04 / RUN-05. What the scheduled job actually does.

PLACEHOLDER, created by plan 03-03 Task 1 only because Task 1's build tests
import this module (from a subprocess, for OPS-02) and monkeypatch
``execute_scheduled_run`` / ``load_config`` / ``get_engine`` on it. The real
behaviour -- trigger classification, overlap-skip, counter recording -- is
implemented by Task 2, which replaces this file wholesale. Until then the
module exposes exactly the import surface those tests need and nothing more.

Kept separate from build.py because APScheduler persists a TEXTUAL reference to
run_scheduled_discovery and re-imports it in a worker thread. The function must
therefore be top-level, zero-argument, and must construct its own sessionmaker --
a sessionmaker passed via add_job(args=...) would fail to pickle on the next
process restart.
"""

from __future__ import annotations

from huntloop.config import Config, load_config
from huntloop.db.base import get_engine, make_session_factory


def execute_scheduled_run(sessionmaker, cfg: Config, *, now=None) -> None:
    """The body of the scheduled job. Real behaviour lands in plan 03-03 Task 2."""
    raise NotImplementedError("execute_scheduled_run is implemented by plan 03-03 Task 2")


def run_scheduled_discovery() -> None:
    """Zero-argument, top-level, picklable entrypoint APScheduler stores by reference."""
    cfg = load_config()
    sessionmaker = make_session_factory(get_engine())
    execute_scheduled_run(sessionmaker, cfg)
