import type { ReactElement } from "react"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, apiPost } from "@/lib/api"
import Criteria from "../Criteria"
import { CoverageCard } from "../onboarding/CoverageCard"
import { EmployerProposalsStep } from "../onboarding/EmployerProposalsStep"
import { HistoryView } from "./HistoryView"

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

const BASE_PAYLOAD = {
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

const VERSIONS = [
  {
    version: 1,
    created_at: "2026-09-10T09:00:00Z",
    source: "describe",
    payload: BASE_PAYLOAD,
  },
  {
    version: 2,
    created_at: "2026-09-11T09:00:00Z",
    source: "manual_edit",
    payload: {
      ...BASE_PAYLOAD,
      compensation_floor: { amount: 90000, currency: "EUR", period: "annual" },
    },
  },
]

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

describe("EmployerProposalsStep", () => {
  beforeEach(() => vi.clearAllMocks())

  it("posts only the checked employer names at the accept gate", async () => {
    const user = userEvent.setup()
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

describe("HistoryView", () => {
  beforeEach(() => vi.clearAllMocks())

  it("renders a field-level diff between the two selected versions", async () => {
    mockedApi.mockImplementation(async (path) => {
      if (path === "/api/criteria/versions") return VERSIONS
      throw new Error(`unexpected GET ${path}`)
    })

    renderWithProviders(<HistoryView />)

    expect(
      await screen.findByText("compensation_floor.amount")
    ).toBeInTheDocument()
    expect(screen.getByText("80000")).toBeInTheDocument()
    expect(screen.getByText("90000")).toBeInTheDocument()
  })
})

describe("Criteria page", () => {
  beforeEach(() => vi.clearAllMocks())

  it("shows the current version count and a history affordance", async () => {
    mockedApi.mockImplementation(async (path) => {
      if (path === "/api/criteria")
        return { current: VERSIONS[1], total_versions: 3 }
      if (path === "/api/criteria/versions") return VERSIONS
      throw new Error(`unexpected GET ${path}`)
    })

    renderWithProviders(<Criteria />)

    expect(await screen.findByText("v2 of 3")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "View history" })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Save new version" })
    ).toBeInTheDocument()
  })
})
