/**
 * Wire shapes shared by the onboarding/criteria surfaces. Colocated here
 * (rather than re-declared per component) so CoverageCard,
 * EmployerProposalsStep, and Criteria agree on one contract. The server
 * routers are the authority for these fields.
 */

export interface Candidate {
  name: string
  reason: string
}

export interface CompanyOut {
  id: string
  name: string
  ats: string | null
  ats_identifier: string | null
  careers_url: string | null
  enabled: boolean
  resolved: boolean
  resolution_status: string
  resolution_detail: string | null
  possibly_stale: boolean
  staleness_message: string | null
  last_job_count: number | null
  last_checked_at: string | null
  consecutive_empty_runs: number
}

export interface CoverageOut {
  added: number
  watchable: number
  needs_attention: number
  resolved: number
}

export interface CriteriaVersionWire {
  version: number
  created_at: string
  source: string | null
  payload: unknown
}

export interface CriteriaCurrentResponse {
  current: CriteriaVersionWire | null
  total_versions: number
}
