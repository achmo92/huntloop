import type { ReactElement } from "react"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, apiPost } from "@/lib/api"
import Criteria from "../Criteria"
import { CoverageCard } from "./CoverageCard"
import { EmployerProposalsStep } from "./EmployerProposalsStep"

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number
    detail: string
    constructor(status: number, detail: string) {
      super(detail)
      this.name = "ApiError"
      this.status = status
      this.detail = detail
    }
  }
  return {
    ApiError,
    api: vi.fn(),
    apiPost: vi.fn(),
    apiPatch: vi.fn(),
    apiPut: vi.fn(),
  }
})

const mockedApi = vi.mocked(api)
const mockedApiPost = vi.mocked(apiPost)

const EXISTING_PAYLOAD = {
  profile_summary: "Senior backend role, remote in the EU",
  seniority_min: "senior",
  seniority_max: "staff",
  posting_age_days: 30,
  locations: {
    eligible_countries: ["DE"],
    eligible_regions: ["EU"],
    preferred_cities: ["Berlin"],
  },
  compensation_floor: { amount: 80000, currency: "EUR", period: "annual" },
  exclusions: { title_keywords: ["intern"], employers: [] },
  work_authorization: {
    countries_authorized: ["DE"],
    requires_sponsorship: false,
  },
  dimension_weights: {
    role_fit: 4,
    seniority_fit: 3,
    employer_fit: 2,
    trajectory: 1,
  },
}

const EXISTING_RESPONSE = {
  current: {
    version: 2,
    created_at: "2026-09-11T09:00:00Z",
    source: "manual_edit",
    payload: EXISTING_PAYLOAD,
  },
  total_versions: 2,
}

const SUGGESTED = {
  profile_summary: "Staff product engineer in the UK, no on-call",
  seniority_min: "staff",
  seniority_max: null,
  posting_age_days: 14,
  locations: {
    eligible_countries: ["GB"],
    eligible_regions: null,
    preferred_cities: ["London"],
  },
  compensation_floor: { amount: 90000, currency: "GBP", period: "annual" },
  exclusions: { title_keywords: ["manager"], employers: null },
  work_authorization: {
    countries_authorized: ["GB"],
    requires_sponsorship: false,
  },
  dimension_weights: {
    role_fit: 4,
    seniority_fit: 2,
    employer_fit: 1,
    trajectory: 3,
  },
}

const CANDIDATES = [
  { name: "Acme", reason: "Hires backend engineers remotely" },
  { name: "Globex", reason: "Matches your stack" },
  { name: "Initech", reason: "Has a Berlin office" },
]

function renderWithProviders(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe("Criteria intake flow (merged page)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("describes once and prefills the editable form from the response", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue({ current: null, total_versions: 0 })
    mockedApiPost.mockResolvedValueOnce({ suggested: SUGGESTED })
    renderWithProviders(<Criteria />)

    const textarea = await screen.findByLabelText("Your description")
    await user.type(textarea, "Staff product engineer in the UK")
    await user.click(screen.getByRole("button", { name: "Continue" }))

    expect(mockedApiPost).toHaveBeenCalledWith("/api/criteria/describe", {
      text: "Staff product engineer in the UK",
    })
    expect(
      await screen.findByDisplayValue(SUGGESTED.profile_summary)
    ).toBeInTheDocument()

    // D-01: exactly one describe call — corrections happen in the form.
    expect(mockedApiPost).toHaveBeenCalledTimes(1)
  })

  it("saves via POST /api/criteria and reaches the employer-proposal step", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue({ current: null, total_versions: 0 })
    mockedApiPost.mockImplementation(async (path) => {
      if (path === "/api/criteria/describe") return { suggested: SUGGESTED }
      if (path === "/api/criteria") return { version: 1 }
      if (path === "/api/onboarding/propose-employers") return { candidates: [] }
      return {}
    })
    renderWithProviders(<Criteria />)

    const textarea = await screen.findByLabelText("Your description")
    await user.type(textarea, "Staff product engineer in the UK")
    await user.click(screen.getByRole("button", { name: "Continue" }))

    await screen.findByDisplayValue(SUGGESTED.profile_summary)
    await user.click(screen.getByRole("button", { name: "Save and continue" }))

    expect(await screen.findByText(/Saved as version 1/)).toBeInTheDocument()
    expect(mockedApiPost).toHaveBeenCalledWith(
      "/api/criteria",
      expect.objectContaining({ profile_summary: SUGGESTED.profile_summary })
    )
    expect(await screen.findByText("Employers to watch")).toBeInTheDocument()
  })

  it("shows an inline currency error and blocks submit", async () => {
    const user = userEvent.setup()
    // Currency is a picker now (GAP-2), so the invalid value is hand-forced in
    // the loaded payload rather than typed. The zod authority is unchanged.
    mockedApi.mockResolvedValue({
      current: {
        ...EXISTING_RESPONSE.current,
        payload: {
          ...EXISTING_PAYLOAD,
          compensation_floor: {
            amount: 80000,
            currency: "ZZ",
            period: "annual",
          },
        },
      },
      total_versions: 2,
    })
    renderWithProviders(<Criteria />)

    await screen.findByLabelText("Currency")
    await user.click(screen.getByRole("button", { name: "Save new version" }))

    expect(
      await screen.findByText("Use a 3-letter code like USD or EUR")
    ).toBeInTheDocument()
    expect(mockedApiPost).not.toHaveBeenCalled()
  })

  it("renders every editable field group (locations, seniority, comp, exclusions, auth)", async () => {
    mockedApi.mockResolvedValue(EXISTING_RESPONSE)
    renderWithProviders(<Criteria />)

    await screen.findByLabelText("Currency")
    expect(screen.getByText("Eligible countries")).toBeInTheDocument()
    expect(screen.getByText("Minimum level")).toBeInTheDocument()
    expect(screen.getByText("Compensation floor")).toBeInTheDocument()
    expect(screen.getByText("Title keywords to avoid")).toBeInTheDocument()
    expect(
      screen.getByText("Countries you're authorized to work in")
    ).toBeInTheDocument()
    expect(screen.getByText("I need sponsorship")).toBeInTheDocument()
    expect(screen.getByText("Priority order")).toBeInTheDocument()
  })
})

describe("EmployerProposalsStep", () => {
  beforeEach(() => vi.clearAllMocks())

  it("posts only the checked employer names at the accept gate", async () => {
    const user = userEvent.setup()
    mockedApi.mockImplementation(async (path) => {
      if (path === "/api/companies") return []
      throw new Error(`unexpected GET ${path}`)
    })
    mockedApiPost.mockImplementation(async (path) => {
      if (path === "/api/onboarding/propose-employers")
        return { candidates: CANDIDATES }
      if (path === "/api/companies/batch")
        return {
          added: [
            { id: "1", name: "Acme", resolved: false, resolution_status: "needs_attention" },
            { id: "2", name: "Initech", resolved: false, resolution_status: "needs_attention" },
          ],
        }
      return {}
    })

    renderWithProviders(<EmployerProposalsStep />)

    const globex = await screen.findByRole("checkbox", { name: "Globex" })
    expect(screen.getByRole("checkbox", { name: "Acme" })).toBeChecked()

    await user.click(globex)
    await user.click(screen.getByRole("button", { name: "Add 2 employers" }))

    expect(mockedApiPost).toHaveBeenCalledWith("/api/companies/batch", {
      names: ["Acme", "Initech"],
    })
  })
})

describe("CoverageCard", () => {
  beforeEach(() => vi.clearAllMocks())

  it("states the honest headline from the coverage endpoint", async () => {
    mockedApi.mockImplementation(async (path) => {
      if (path === "/api/companies/coverage")
        return { added: 4, watchable: 3, needs_attention: 1, resolved: 3 }
      if (path === "/api/companies")
        return [
          {
            id: "1",
            name: "Globex",
            ats: null,
            ats_identifier: null,
            careers_url: null,
            enabled: true,
            resolved: false,
            resolution_status: "needs_attention",
            resolution_detail: "No job board found",
            possibly_stale: false,
            staleness_message: null,
            last_job_count: null,
            last_checked_at: null,
            consecutive_empty_runs: 0,
          },
        ]
      throw new Error(`unexpected GET ${path}`)
    })

    renderWithProviders(<CoverageCard />)

    expect(
      await screen.findByText(/watching 3 of your 4 added employers/)
    ).toBeInTheDocument()
    expect(
      screen.getByText(/we can't automatically watch 1 yet/i)
    ).toBeInTheDocument()
  })
})
