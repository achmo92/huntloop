# Data Model

Schema for HuntLoop. Every instance is independently self-hosted — locally or on the operator's own cloud — so nothing here assumes a specific cloud provider.

## Database portability

**SQLAlchemy + Alembic from the first commit**, with a `DATABASE_URL` environment variable defaulting to `sqlite:///data/huntloop.db`. Postgres is supported as a first-class alternative.

This is cheap to design in and expensive to retrofit, and there is a deployment case beyond preference: any container platform without persistent volumes (Fargate, Cloud Run, Fly without a volume) makes SQLite unworkable.

**Honest framing:** SQLite with WAL enabled works across two containers sharing a local volume, but it is the weakest link in a hosted setup. Treat SQLite as the default for local and single-machine deployments, and Postgres as the recommended option when deploying to a cloud.

### Portability rules

- SQLAlchemy Core/ORM only — no raw `sqlite3`, no `INSERT OR REPLACE`, no reliance on `AUTOINCREMENT` behaviour
- UUID primary keys stored via SQLAlchemy's native `Uuid` type — avoids divergence between Postgres sequences and SQLite rowids
- `JSON` column type (`JSON().with_variant(JSONB, "postgresql")`) so it maps to SQLite JSON1 and Postgres JSONB automatically
- Enums as TEXT with `Enum(..., native_enum=False)` and a CHECK constraint, validated in application code — avoids Postgres's transaction-unsafe `ALTER TYPE ADD VALUE` migration pain
- **Always store UTC**, converted at display time — SQLite does not enforce timezone awareness
- Alembic migrations from the start, even for a single-user deployment

## Filter tiers referenced below

Listings pass through three stages, and several columns record where a listing stopped:

1. **deterministic** — free, no model call: posting age, location eligibility, hard exclusions, title keyword matching
2. **triage** — a cheap or local model making a coarse relevant/not-relevant judgement
3. **full** — the complete scoring pass against the rubric, run only on survivors

## Tables

```sql
-- Versioned, because the feedback loop proposes changes to it over time
-- and scores must remain comparable across versions
criteria
  id                 uuid pk
  version            int not null            -- monotonic
  is_active          bool not null           -- exactly one row true
  payload            json not null           -- structured intake fields + prose guidance + weights
  source             text                    -- initial | proposal_accepted | manual_edit
  created_at         timestamptz

-- The propose-then-approve mechanism, made concrete
criteria_proposals
  id                 uuid pk
  based_on_run_id    uuid fk -> runs
  proposed_changes   json not null           -- diff against the active criteria
  rationale          text not null           -- the observed pattern that motivated it
  evidence           json                    -- job ids and status events supporting it
  predicted_effect   json                    -- e.g. would surface N more, suppress M
  status             text                    -- pending | accepted | rejected
  decided_at         timestamptz
  resulting_version  int fk -> criteria.version
  created_at         timestamptz

companies
  id                     uuid pk
  name                   text not null
  ats                    text     -- greenhouse|lever|ashby|workday|smartrecruiters|firecrawl|unknown
  ats_identifier         text     -- the employer's public board slug
  ats_config             json     -- per-ATS extras, e.g. Workday tenant and site
  careers_url            text
  enabled                bool default true
  resolved_at            timestamptz
  last_checked_at        timestamptz
  last_job_count         int
  consecutive_empty_runs int default 0
```

**On `ats_identifier`:** this is the employer's public slug as it appears in their job board URL, and **it is frequently not the company name** — a company may publish under a legal-entity slug bearing no resemblance to its trading name. Guessing it fails often enough that a resolution step is mandatory rather than a convenience.

**On `consecutive_empty_runs`:** registries decay. Employers change ATS, get acquired, or rename their board, and the symptom is silent — the company simply stops returning listings. Counting consecutive empty results turns that silence into an alert. This must only increment on a confirmed genuinely-empty response (200 with zero listings), never on a request that errored or was rate-limited — those are a different failure mode and must not be conflated with registry decay.

```sql
jobs
  id                      uuid pk
  company_id              uuid fk -> companies
  dedup_key               text unique not null  -- "company:external_id", else normalised URL
  external_id             text                  -- the ATS's own job id, when available
  url                     text not null
  title                   text not null
  location_raw            text                  -- exactly as the source provided it
  location_normalized     text
  is_remote               bool
  remote_scope            text    -- global | region | country | unspecified
  location_eligible       bool    -- satisfies the user's configured locations
  posted_at               timestamptz
  first_seen_at           timestamptz
  last_seen_at            timestamptz
  description             text                  -- full posting text; fetched only for full scoring
  comp_raw                text                  -- exactly as stated, unparsed
  comp_min                numeric
  comp_max                numeric
  comp_currency           text                  -- ISO 4217
  comp_period             text                  -- annual | monthly | hourly | unspecified
  work_auth_required      text                  -- as stated, e.g. a regional work-rights requirement
  status                  text  -- new|shortlisted|applied|interviewing|offer|rejected|withdrawn
  score_overall           numeric               -- COMPUTED from dimensions x weights
  score_dimensions        json                  -- per-dimension scores and reasons
  score_flags             json                  -- advisory flags, do not affect the score
  score_summary           text                  -- short summary for the jobs list
  scored_at               timestamptz
  scored_criteria_version int
  scored_rubric_version   text
  scored_with_model       text
  filter_tier_reached     text  -- deterministic | triage | full
  user_notes              text
  source_run_id           uuid fk -> runs
```

**On location eligibility:** a posting marked remote is not automatically open to the user. Remote roles are frequently scoped to a single country or region, so a role that is remote-but-restricted-elsewhere must not pass as remote. `remote_scope` records what the posting claims; `location_eligible` records the resolved answer against the user's configured locations.

Location parsing must be **deterministic**, not a model call — it sits in the free, pre-model filter tier, and an LLM call per listing at that tier would defeat the tiering's purpose. Use a structured gazetteer/library approach (e.g. `pycountry` plus a hand-built location table), not per-country regex, since the product must generalise to any geography.

**On compensation:** stated formats vary by region — annual, monthly, hourly, and local conventions such as lakhs-per-annum. Store the raw string alongside the parsed values so a parsing error is always recoverable and auditable. When a criteria floor and a posting are in different currencies, either convert explicitly or mark the comparison as not possible; a silent mis-comparison across currencies is worse than declining to compare.

```sql
-- The implicit feedback signal the loop depends on
status_events
  id            uuid pk
  job_id        uuid fk -> jobs
  from_status   text
  to_status     text
  changed_at    timestamptz
  source        text    -- user | system

-- Explicit feedback
feedback_notes
  id          uuid pk
  job_id      uuid fk -> jobs NULL           -- null means general, not about one job
  text        text not null
  source      text    -- chat | job_note
  created_at  timestamptz

runs
  id                  uuid pk
  trigger             text    -- scheduled | manual
  status              text    -- running | success | partial | failed
  started_at          timestamptz
  finished_at          timestamptz
  companies_checked   int
  listings_fetched    int
  after_dedup         int
  after_deterministic int
  after_triage        int
  scored              int
  new_jobs_written    int
  tokens_in           int
  tokens_out          int
  cost_usd            numeric
  error_summary       text

run_errors
  id          uuid pk
  run_id      uuid fk -> runs
  company_id  uuid fk -> companies NULL
  stage       text
  message     text

settings
  key         text pk
  value       json not null
  is_secret   bool default false
```

## Design notes

- **`status_events` is not redundant with `jobs.status`.** The adaptive loop needs *dwell time* — how long a listing sat unactioned before being shortlisted, and which stage rejections happen at. A single status column cannot express that, and it is the richest signal available without asking the user to do extra work.
- **`scored_criteria_version` and `scored_rubric_version`** keep scores interpretable after criteria or rubric changes. Without them, scores produced under different rubrics get compared as though equivalent. LLM scores are not reproducible even at temperature 0, and providers silently update models behind pinned names, which is why both the criteria version and the rubric/scoring-config version must be stamped on every score, not just one of them.
- **Funnel counts on `runs`** (fetched → dedup → deterministic → triage → scored) show exactly where volume is shed at each stage, which is how the deterministic filter gets tuned. Since token spend is driven almost entirely by how many listings reach the scoring stage, this is the primary cost-control instrument.
- **`dedup_key` is a single unique column** rather than a composite constraint, because `external_id` is null for listings discovered by crawling rather than through an ATS API. It must be computed deterministically in application code from a stable identifier (ATS external ID when available, else a normalised URL) so that re-running discovery never creates duplicate rows — this is a hard requirement, not an optimisation.
- **`description` is nullable and lazily populated** — fetched only for listings that reach full scoring, not for every listing discovered.
- **Flags do not affect `score_overall`.** They are advisory and displayed separately from the computed score.

## Secrets

API keys are entered through the settings screen, and the data volume gets backed up. That means **a leaked backup leaks the keys** if they are stored as plaintext in the same database file.

Store secrets encrypted with a key supplied via an environment variable at deploy time (`HUNTLOOP_SECRET_KEY`), kept out of the backup. Entry through the UI still works, and a stolen backup is useless without the deploy-time key. This is what `settings.is_secret` marks.

Each deployment manages its own secret key. Nothing is shared between instances.
