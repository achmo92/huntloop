export interface ApiAccess {
  base_url: string
  has_api_key: boolean
  api_key_reentry_required: boolean
}

export interface Models {
  triage: string
  scoring: string
  extraction: string
}

export interface Schedule {
  run_at: string
  timezone: string
}

export interface SpendCap {
  cap_usd: number | null
}

export interface SettingsResponse {
  api_access: ApiAccess
  models: Models
  schedule: Schedule
  spend_cap: SpendCap
}
