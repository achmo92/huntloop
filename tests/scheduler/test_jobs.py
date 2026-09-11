"""RUN-03 / RUN-05 / RUN-08 scheduled-job tests. Owned by plans 03-03 and 03-04.

Owning test names (03-VALIDATION.md): test_overlap_skip_records_dedicated_run_row,
test_scheduled_run_populates_stage_counts, test_run_status_capped_with_reason.
"""
import pytest

jobs = pytest.importorskip("huntloop.scheduler.jobs")


def test_scheduled_job_entrypoint_takes_no_arguments():
    """APScheduler persists a textual func ref and cannot pass a sessionmaker."""
    import inspect

    assert list(inspect.signature(jobs.run_scheduled_discovery).parameters) == []
