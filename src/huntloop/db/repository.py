"""Main-store repository layer.

Every write goes through `huntloop.db.upsert.upsert()` with an explicit
per-table `update_columns` allowlist -- no ORM merge-on-conflict, no blind
INSERT on a keyed table, no SELECT-then-branch `get_or_create` (a TOCTOU
race under any concurrent writer). See 01-RESEARCH.md Pattern 1 / Pitfall A
and .planning/research/PITFALLS.md Pitfall 5: a re-run must never revert a
manually advanced status.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from huntloop.db.models import Company, Job, JobStatus, Run, RunError, RunStatus, RunTrigger, Setting, StatusEvent, StatusEventSource
from huntloop.db.upsert import upsert

# Discovery-owned columns on `jobs`: refreshed on every re-discovery of the
# same dedup_key. Deliberately EXCLUDES user-owned state (status, user_notes),
# system-computed scoring state (score_*, scored_*), the pipeline-stage marker
# (filter_tier_reached), the first-discovery provenance (first_seen_at,
# source_run_id) and the natural key itself (dedup_key) and surrogate key (id).
# PITFALLS.md Pitfall 5: a re-run must never revert a manually advanced status.
JOB_DISCOVERY_OWNED_COLUMNS: list[str] = [
    "company_id",
    "external_id",
    "url",
    "title",
    "location_raw",
    "location_normalized",
    "is_remote",
    "remote_scope",
    "location_eligible",
    "posted_at",
    "last_seen_at",
    "description",
    "comp_raw",
    "comp_min",
    "comp_max",
    "comp_currency",
    "comp_period",
    "work_auth_required",
    "filter_tier_reached",
    "source_run_id",
    "first_seen_at",
]

# Registry-owned columns on `companies`. EXCLUDES `enabled` (user-owned:
# REG-06 requires disabling an employer without losing history, so a
# re-resolution must not silently re-enable it) and `name` (the conflict key).
COMPANY_DISCOVERY_OWNED_COLUMNS: list[str] = [
    "ats",
    "ats_identifier",
    "ats_config",
    "careers_url",
    "resolved_at",
    "last_checked_at",
    "last_job_count",
    "consecutive_empty_runs",
]


class CompanyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_by_name(self, name: str, **fields) -> uuid.UUID:
        """Insert or refresh a company keyed on its unique `name`.

        Only fields the caller actually supplied are eligible for update
        (restricted further to COMPANY_DISCOVERY_OWNED_COLUMNS) -- a partial
        call never blanks a column the caller did not mention. The
        generated `id` in `values` is discarded on conflict; the row's real
        id is re-read via `get_by_name()` after the upsert.
        """
        values = {"id": uuid.uuid4(), "name": name, **fields}
        update_columns = [c for c in COMPANY_DISCOVERY_OWNED_COLUMNS if c in fields]
        upsert(
            self.session.connection(),
            Company.__table__,
            index_elements=["name"],
            values=values,
            update_columns=update_columns,
        )
        return self.get_by_name(name).id

    def get_by_name(self, name: str) -> Company | None:
        return self.session.execute(
            select(Company).where(Company.name == name)
        ).scalar_one_or_none()

    def list_enabled(self) -> list[Company]:
        return list(
            self.session.execute(select(Company).where(Company.enabled.is_(True))).scalars()
        )

    def mark_empty_run(self, company_id: uuid.UUID) -> None:
        """Increment consecutive_empty_runs for a CONFIRMED genuinely-empty response.

        Only call this on an HTTP 200 with zero listings. Never call this on
        an errored or rate-limited fetch -- conflating them turns a
        transport failure into a false registry-decay signal
        (docs/architecture/data-model.md, note on consecutive_empty_runs).
        """
        company = self.session.get(Company, company_id)
        company.consecutive_empty_runs += 1
        company.last_checked_at = datetime.now(UTC)
        company.last_job_count = 0

    def mark_listings_found(self, company_id: uuid.UUID, count: int) -> None:
        company = self.session.get(Company, company_id)
        company.consecutive_empty_runs = 0
        company.last_job_count = count
        company.last_checked_at = datetime.now(UTC)


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_discovered(
        self,
        dedup_key: str,
        company_id: uuid.UUID,
        url: str,
        title: str,
        **fields,
    ) -> None:
        """Insert or refresh a job keyed on its unique `dedup_key`.

        Only JOB_DISCOVERY_OWNED_COLUMNS may be passed as extra `fields` --
        passing `status=`, a `score_*` field, or any other user-owned or
        system-computed column raises ValueError immediately, rather than
        silently clobbering it.
        """
        bad_keys = [k for k in fields if k not in JOB_DISCOVERY_OWNED_COLUMNS]
        if bad_keys:
            raise ValueError(
                f"upsert_discovered() received non-discovery-owned field(s): "
                f"{bad_keys}. These columns are user-owned or system-computed "
                "and must never be written by a discovery upsert -- use "
                "set_status()/mark_scored() instead."
            )
        values = {
            "id": uuid.uuid4(),
            "dedup_key": dedup_key,
            "company_id": company_id,
            "url": url,
            "title": title,
            "last_seen_at": datetime.now(UTC),
            **fields,
        }
        update_columns = [c for c in JOB_DISCOVERY_OWNED_COLUMNS if c in values]
        upsert(
            self.session.connection(),
            Job.__table__,
            index_elements=["dedup_key"],
            values=values,
            update_columns=update_columns,
        )

    def get_by_dedup_key(self, dedup_key: str) -> Job | None:
        return self.session.execute(
            select(Job).where(Job.dedup_key == dedup_key)
        ).scalar_one_or_none()

    def list_by_dedup_key(self, dedup_key: str) -> list[Job]:
        return list(
            self.session.execute(select(Job).where(Job.dedup_key == dedup_key)).scalars()
        )

    def count(self) -> int:
        return self.session.execute(select(func.count()).select_from(Job)).scalar_one()

    def set_status(
        self,
        job_id: uuid.UUID,
        to_status: JobStatus,
        source: StatusEventSource = StatusEventSource.USER,
    ) -> None:
        """Update Job.status AND append the paired StatusEvent.

        `jobs.status` must never be mutated without its paired event --
        status_events is the feedback loop's only signal source (dwell
        time, stage-of-rejection).
        """
        job = self.session.get(Job, job_id)
        from_status = job.status
        job.status = to_status
        self.session.add(
            StatusEvent(
                job_id=job_id,
                from_status=from_status,
                to_status=to_status,
                changed_at=datetime.now(UTC),
                source=source,
            )
        )

    def mark_scored(
        self,
        job_id: uuid.UUID,
        *,
        score_overall,
        score_dimensions,
        score_flags,
        score_summary,
        criteria_version,
        rubric_version,
        model,
        filter_tier,
    ) -> None:
        """The ONLY write path allowed to touch score_*/scored_* columns.

        `score_overall` is computed by the caller from dimensions x weights
        and is never requested from a model (SCOR-07). All three version
        stamps (criteria_version, rubric_version, model) are mandatory
        (SCOR-10) -- without them, scores produced under different
        criteria/rubrics get compared as though equivalent.
        """
        job = self.session.get(Job, job_id)
        job.score_overall = score_overall
        job.score_dimensions = score_dimensions
        job.score_flags = score_flags
        job.score_summary = score_summary
        job.scored_criteria_version = criteria_version
        job.scored_rubric_version = rubric_version
        job.scored_with_model = model
        job.filter_tier_reached = filter_tier
        job.scored_at = datetime.now(UTC)


class SettingsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def set_value(self, key: str, value) -> None:
        existing = self.session.get(Setting, key)
        if existing is not None and existing.is_secret:
            raise ValueError(
                f"{key} is marked is_secret; write it through CredentialStore, not settings"
            )
        upsert(
            self.session.connection(),
            Setting.__table__,
            index_elements=["key"],
            values={"key": key, "value": value, "is_secret": False},
            update_columns=["value", "is_secret"],
        )

    def set_secret_metadata(self, key: str) -> None:
        """Record that `key` exists and is sensitive.

        The ciphertext lives ONLY in the credentials store; this row records
        only that the key exists and is sensitive, never the value itself.
        """
        upsert(
            self.session.connection(),
            Setting.__table__,
            index_elements=["key"],
            values={"key": key, "value": None, "is_secret": True},
            update_columns=["value", "is_secret"],
        )

    def get_value(self, key: str):
        setting = self.session.get(Setting, key)
        return setting.value if setting is not None else None

    def is_secret(self, key: str) -> bool:
        setting = self.session.get(Setting, key)
        return bool(setting.is_secret) if setting is not None else False


class RunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start(self, trigger: RunTrigger) -> Run:
        run = Run(
            id=uuid.uuid4(),
            trigger=trigger,
            status=RunStatus.RUNNING,
            started_at=datetime.now(UTC),
            companies_checked=0,
            listings_fetched=0,
            after_dedup=0,
            after_deterministic=0,
            after_triage=0,
            scored=0,
            new_jobs_written=0,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def record_error(self, run_id: uuid.UUID, company_id: uuid.UUID, stage: str, message: str) -> RunError:
        error = RunError(
            id=uuid.uuid4(),
            run_id=run_id,
            company_id=company_id,
            stage=stage,
            message=message[:2000],  # truncate to prevent bloat
        )
        self.session.add(error)
        self.session.flush()
        return error

    def finish(
        self,
        run_id: uuid.UUID,
        *,
        status: RunStatus,
        companies_checked: int,
        listings_fetched: int,
        after_dedup: int,
        after_deterministic: int,
        after_triage: int,
        scored: int,
        new_jobs_written: int,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        error_summary: str | None = None,
    ) -> Run:
        run = self.session.get(Run, run_id)
        run.status = status
        run.companies_checked = companies_checked
        run.listings_fetched = listings_fetched
        run.after_dedup = after_dedup
        run.after_deterministic = after_deterministic
        run.after_triage = after_triage
        run.scored = scored
        run.new_jobs_written = new_jobs_written
        run.tokens_in = tokens_in
        run.tokens_out = tokens_out
        run.cost_usd = cost_usd
        if error_summary is not None:
            run.error_summary = error_summary
        run.finished_at = datetime.now(UTC)
        return run

    def get(self, run_id: uuid.UUID) -> Run | None:
        return self.session.get(Run, run_id)

    def list_recent(self, limit: int = 10) -> list[Run]:
        return list(
            self.session.execute(
                select(Run).order_by(Run.started_at.desc()).limit(limit)
            ).scalars()
        )
