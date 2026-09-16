/**
 * Wire shapes for the proposals review surface (UI-03). These mirror
 * `src/huntloop/api/routers/proposals.py` field for field — snake_case
 * preserved, because the API is the authority and nothing in this codebase
 * camelises a response. Colocated here (rather than re-declared per component)
 * so ProposalCard, ProposalsPage and their tests agree on one contract.
 */

export type ProposalStatus = "pending" | "accepted" | "rejected"

/** LOOP-09: one prior decision on the same edit signature (field + direction). */
export interface PriorRejection {
  decided_at: string
  rejection_reason: string | null
}

/** D-06a: a proposal touches exactly one dotted leaf path. */
export interface ProposedChanges {
  field: string
  direction: "tighten" | "loosen" | "increase" | "decrease"
  current_value: unknown
  proposed_value: unknown
}

export interface EvidenceTransition {
  from: string | null
  to: string
  changed_at: string
}

/** LOOP-05: the concrete listing that proves the pattern. */
export interface EvidenceListing {
  job_id: string
  title: string
  company: string
  status: string
  dwell_days: number | null
  transitions: EvidenceTransition[]
  attributed_to: string | null
}

export interface EvidenceQuote {
  note_id: string
  quote: string
}

export interface Evidence {
  signal: string
  observation_count: number
  threshold: number
  truncated: boolean
  listings: EvidenceListing[]
  feedback_quotes: EvidenceQuote[]
}

/**
 * LOOP-06 / 05-UI-SPEC: the predicted effect is a discriminated union over the
 * four payload kinds `predicted_effect.py::FIELD_TIERS` can produce. The
 * `kind` discriminant is what `describePredictedEffect` switches on, so a new
 * tier fails `tsc` rather than rendering an empty row.
 */
export type PredictedEffect =
  | {
      kind: "filter_dry_run"
      field: string
      would_exclude: number
      would_include: number
      backlog_size: number
      approximate: boolean
      sample: { job_id: string; title: string; reason: string }[]
    }
  | {
      kind: "flag_recompute"
      field: string
      flag: string
      would_flag: number
      would_unflag: number
      unknown: number
      backlog_size: number
    }
  | {
      kind: "literal_count"
      field: string
      term: string | null
      matches: number
      enforced: boolean
      backlog_size: number
      note: string
    }
  | {
      kind: "score_recompute"
      field: string
      affected: number
      mean_delta: number
      max_delta: number
      backlog_size: number
      skipped: number
    }

/** `GET /api/proposals` item — mirrors `ProposalOut` exactly. */
export interface ProposalOut {
  id: string
  created_at: string
  status: ProposalStatus
  based_on_run_id: string | null
  proposed_changes: ProposedChanges
  rationale: string
  evidence: Evidence | null
  predicted_effect: PredictedEffect | null
  decided_at: string | null
  resulting_version: number | null
  rejection_reason: string | null
  prior_rejections: PriorRejection[]
}
