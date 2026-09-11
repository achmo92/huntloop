"""RUN-01 / RUN-04 / OPS-02 scheduler-build tests. Owned by plan 03-03.

Owning test names (03-VALIDATION.md): test_cron_trigger_fires_at_configured_local_time,
test_catchup_fires_once_after_multi_day_gap, test_existing_job_not_readded_on_restart,
test_scheduler_module_has_no_playwright_import.
"""
import pytest

build = pytest.importorskip("huntloop.scheduler.build")


def test_apscheduler_is_pinned_to_the_locked_version():
    import apscheduler

    assert apscheduler.__version__ == "3.11.3"
