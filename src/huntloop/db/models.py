"""Main-store ORM models: all nine tables from docs/architecture/data-model.md.

This is the schema both the agent container (Phase 2+) and the API container
(Phase 4) import. Every model subclasses the shared `Base` from
`huntloop.db.base` -- no separate `DeclarativeBase` is declared here.

Only the four portable patterns from .planning/research/STACK.md Question 5
are used: `sqlalchemy.Uuid` (inferred from `uuid.UUID` annotations) for
primary keys, `JSON().with_variant(JSONB, "postgresql")` for JSON columns,
`Enum(..., native_enum=False, validate_strings=True)` for every enum column,
and `DateTime(timezone=True)` for every timestamp, defaulted via the
tz-aware `datetime.now(timezone.utc)` -- never the naive UTC clock.
"""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from huntloop.db.base import Base

__all__ = [
    # Enums
    "AtsPlatform",
    "CompPeriod",
    "Company",
    # Tables
    "Criteria",
    "CriteriaProposal",
    "CriteriaSource",
    "FeedbackNote",
    "FeedbackSource",
    "FilterTier",
    "Job",
    "JobStatus",
    "ProposalStatus",
    "RemoteScope",
    "Run",
    "RunError",
    "RunStatus",
    "RunTrigger",
    "Setting",
    "StatusEvent",
    "StatusEventSource",
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Portable tz-aware timestamp: `DateTime(timezone=True)` under the hood,
    with the tzinfo round-trip SQLite otherwise drops made explicit.

    SQLite has no native "timestamp with time zone" type -- SQLAlchemy's
    generic `DateTime(timezone=True)` is honored by Postgres (maps to
    `TIMESTAMP WITH TIME ZONE`, which returns tz-aware values) but on
    SQLite the offset is silently discarded on write and the value comes
    back naive on read. `docs/architecture/data-model.md` already commits
    this project to "Always store UTC" as an application-level discipline;
    this type makes that discipline load-bearing instead of aspirational:
    every write is normalised to UTC (and a naive input is rejected, since
    "which timezone" would otherwise be a silent guess), and every read
    that comes back naive (the SQLite case) is reattached UTC tzinfo. On
    Postgres, where the driver already returns a tz-aware value, this is a
    no-op re-normalisation. Discovered while writing plan 01-05's OPS-04
    portability suite -- see 01-05-SUMMARY.md.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "UTCDateTime received a naive datetime -- every write must supply "
                "a tz-aware value (docs/architecture/data-model.md: 'Always store UTC')."
            )
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class AtsPlatform(str, enum.Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    WORKDAY = "workday"
    SMARTRECRUITERS = "smartrecruiters"
    FIRECRAWL = "firecrawl"
    UNKNOWN = "unknown"


class JobStatus(str, enum.Enum):
    NEW = "new"
    SHORTLISTED = "shortlisted"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class RemoteScope(str, enum.Enum):
    GLOBAL = "global"
    REGION = "region"
    COUNTRY = "country"
    UNSPECIFIED = "unspecified"


class CompPeriod(str, enum.Enum):
    ANNUAL = "annual"
    MONTHLY = "monthly"
    HOURLY = "hourly"
    UNSPECIFIED = "unspecified"


class FilterTier(str, enum.Enum):
    DETERMINISTIC = "deterministic"
    TRIAGE = "triage"
    FULL = "full"


class CriteriaSource(str, enum.Enum):
    INITIAL = "initial"
    PROPOSAL_ACCEPTED = "proposal_accepted"
    MANUAL_EDIT = "manual_edit"


class ProposalStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RunTrigger(str, enum.Enum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class StatusEventSource(str, enum.Enum):
    USER = "user"
    SYSTEM = "system"


class FeedbackSource(str, enum.Enum):
    CHAT = "chat"
    JOB_NOTE = "job_note"


# --------------------------------------------------------------------------
# criteria
# --------------------------------------------------------------------------


class Criteria(Base):
    """Versioned scoring criteria. Exactly one row has is_active=True.

    Versioned because the feedback loop proposes changes to it over time and
    scores must remain comparable across versions (scored_criteria_version
    on `jobs` stamps which version produced a given score).
    """

    __tablename__ = "criteria"
    __table_args__ = (
        UniqueConstraint("version", name="uq_criteria_version"),
        # Portable "exactly one row true" enforcement: a partial unique index
        # is supported by both SQLite and Postgres, and passing both
        # sqlite_where and postgresql_where on one Index is the portable
        # idiom (01-RESEARCH.md / STACK.md Question 5).
        Index(
            "uq_criteria_single_active",
            "is_active",
            unique=True,
            sqlite_where=sa_text("is_active = 1"),
            postgresql_where=sa_text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version: Mapped[int] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=False)
    payload: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    source: Mapped[CriteriaSource | None] = mapped_column(
        SAEnum(
            CriteriaSource,
            native_enum=False,
            validate_strings=True,
            name="criteria_source",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, nullable=False
    )


class CriteriaProposal(Base):
    """The propose-then-approve mechanism, made concrete."""

    __tablename__ = "criteria_proposals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    based_on_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id"), nullable=True
    )
    proposed_changes: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    predicted_effect: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    status: Mapped[ProposalStatus] = mapped_column(
        SAEnum(
            ProposalStatus,
            native_enum=False,
            validate_strings=True,
            name="proposal_status",
        ),
        default=ProposalStatus.PENDING,
        nullable=False,
    )
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resulting_version: Mapped[int | None] = mapped_column(
        ForeignKey("criteria.version"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, nullable=False
    )


# --------------------------------------------------------------------------
# companies
# --------------------------------------------------------------------------


# Documented deviation from docs/architecture/data-model.md: the canonical
# doc lists no unique constraint on `companies`. One is REQUIRED here because
# guardrail #1 mandates upsert-based writes and ON CONFLICT needs a conflict
# target; `name` is also the natural key the user supplies (REG-01: "add an
# employer by name or careers URL"). Do not remove this constraint.
class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("name", name="uq_companies_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ats: Mapped[AtsPlatform | None] = mapped_column(
        SAEnum(AtsPlatform, native_enum=False, validate_strings=True, name="ats_platform"),
        nullable=True,
    )
    # The employer's PUBLIC BOARD SLUG, frequently NOT the trading name.
    ats_identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    ats_config: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    careers_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # USER-OWNED: never touched by a re-run upsert (REG-06).
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    last_job_count: Mapped[int | None] = mapped_column(nullable=True)
    consecutive_empty_runs: Mapped[int] = mapped_column(default=0, nullable=False)


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_jobs_dedup_key"),
        Index("ix_jobs_company_id", "company_id"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_score_overall", "score_overall"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    # "company:external_id", else a normalised URL. Computed deterministically
    # in application code -- this is a hard requirement, not an optimisation.
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    location_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_remote: Mapped[bool | None] = mapped_column(nullable=True)
    # What the posting CLAIMS.
    remote_scope: Mapped[RemoteScope | None] = mapped_column(
        SAEnum(RemoteScope, native_enum=False, validate_strings=True, name="remote_scope"),
        nullable=True,
    )
    # The RESOLVED answer against user locations.
    location_eligible: Mapped[bool | None] = mapped_column(nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, nullable=False
    )
    # Lazily populated, only for listings reaching full scoring.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    comp_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    comp_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    comp_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    comp_currency: Mapped[str | None] = mapped_column(Text, nullable=True)
    comp_period: Mapped[CompPeriod | None] = mapped_column(
        SAEnum(CompPeriod, native_enum=False, validate_strings=True, name="comp_period"),
        nullable=True,
    )
    work_auth_required: Mapped[str | None] = mapped_column(Text, nullable=True)
    # USER-OWNED.
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, native_enum=False, validate_strings=True, name="job_status"),
        default=JobStatus.NEW,
        nullable=False,
    )
    # COMPUTED from dimensions x weights, never asked of a model (SCOR-07).
    score_overall: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    # Per-dimension scores + one-line reasons for role_fit, seniority_fit,
    # employer_fit, trajectory.
    score_dimensions: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    # Advisory only, never folded into score_overall: stretch_role,
    # step_down, language_requirement, location_ambiguity, comp_below_floor,
    # posting_stale.
    score_flags: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    score_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    scored_criteria_version: Mapped[int | None] = mapped_column(nullable=True)
    scored_rubric_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    scored_with_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    filter_tier_reached: Mapped[FilterTier | None] = mapped_column(
        SAEnum(FilterTier, native_enum=False, validate_strings=True, name="filter_tier"),
        nullable=True,
    )
    # USER-OWNED.
    user_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"), nullable=True)


# --------------------------------------------------------------------------
# status_events / feedback_notes
# --------------------------------------------------------------------------


class StatusEvent(Base):
    """The implicit feedback signal the loop depends on.

    Not redundant with `jobs.status` -- the adaptive loop needs dwell time
    (how long a listing sat unactioned) and stage-of-rejection, which a
    single status column cannot express.
    """

    __tablename__ = "status_events"
    __table_args__ = (Index("ix_status_events_job_id", "job_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    from_status: Mapped[JobStatus | None] = mapped_column(
        SAEnum(
            JobStatus,
            native_enum=False,
            validate_strings=True,
            name="status_event_from_status",
        ),
        nullable=True,
    )
    to_status: Mapped[JobStatus] = mapped_column(
        SAEnum(
            JobStatus,
            native_enum=False,
            validate_strings=True,
            name="status_event_to_status",
        ),
        nullable=False,
    )
    changed_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, nullable=False
    )
    source: Mapped[StatusEventSource] = mapped_column(
        SAEnum(
            StatusEventSource,
            native_enum=False,
            validate_strings=True,
            name="status_event_source",
        ),
        nullable=False,
        default=StatusEventSource.USER,
    )


class FeedbackNote(Base):
    """Explicit feedback. `job_id` NULL means general feedback, not about one job."""

    __tablename__ = "feedback_notes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    # Note: `text` collides with `sqlalchemy.text` if imported bare -- see
    # the `sa_text` import above, used in the criteria partial-index clauses.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[FeedbackSource | None] = mapped_column(
        SAEnum(FeedbackSource, native_enum=False, validate_strings=True, name="feedback_source"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, nullable=False
    )


# --------------------------------------------------------------------------
# runs / run_errors
# --------------------------------------------------------------------------


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trigger: Mapped[RunTrigger] = mapped_column(
        SAEnum(RunTrigger, native_enum=False, validate_strings=True, name="run_trigger"),
        nullable=False,
    )
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, native_enum=False, validate_strings=True, name="run_status"),
        default=RunStatus.RUNNING,
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Funnel counters (fetched -> dedup -> deterministic -> triage -> scored):
    # separate columns with default=0, not one aggregate -- RUN-05 requires
    # per-stage counts, and a NULL counter is indistinguishable from a
    # genuine zero.
    companies_checked: Mapped[int] = mapped_column(default=0, nullable=False)
    listings_fetched: Mapped[int] = mapped_column(default=0, nullable=False)
    after_dedup: Mapped[int] = mapped_column(default=0, nullable=False)
    after_deterministic: Mapped[int] = mapped_column(default=0, nullable=False)
    after_triage: Mapped[int] = mapped_column(default=0, nullable=False)
    scored: Mapped[int] = mapped_column(default=0, nullable=False)
    new_jobs_written: Mapped[int] = mapped_column(default=0, nullable=False)
    tokens_in: Mapped[int] = mapped_column(default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(default=0, nullable=False)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class RunError(Base):
    __tablename__ = "run_errors"
    __table_args__ = (Index("ix_run_errors_run_id", "run_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True
    )
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


# Documented deviation from docs/architecture/data-model.md: the canonical
# doc declares `settings.value` as `json not null`. It is nullable here,
# with a CHECK constraint forbidding a non-null value when `is_secret` is
# true. This is required by the stronger locked decision in 01-CONTEXT.md:
# secrets live in a *separate encrypted store*, not in this file. The
# `settings` row is metadata only -- "this config key exists and it is
# sensitive" -- while the ciphertext lives exclusively in credentials.db.
class Setting(Base):
    __tablename__ = "settings"
    __table_args__ = (
        CheckConstraint(
            "NOT (is_secret AND value IS NOT NULL)",
            name="ck_settings_secret_value_null",
        ),
    )

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    # none_as_null=True on BOTH sides of the variant: SQLAlchemy's JSON type
    # defaults to persisting a Python None as the JSON literal "null" (a
    # non-NULL stored value), not SQL NULL. That default would silently
    # defeat the ck_settings_secret_value_null CHECK constraint below the
    # first time set_secret_metadata() writes value=None. See Deviations in
    # 01-02-SUMMARY.md.
    value: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
        nullable=True,
    )
    is_secret: Mapped[bool] = mapped_column(default=False, nullable=False)
