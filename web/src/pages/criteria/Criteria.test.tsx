import type { ReactElement } from "react"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, apiPost } from "@/lib/api"
import Criteria from "../Criteria"
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

  it("saves an edit as a new version (edit round-trip)", async () => {
    const user = userEvent.setup()
    mockedApi.mockImplementation(async (path) => {
      if (path === "/api/criteria")
        return { current: VERSIONS[1], total_versions: 3 }
      throw new Error(`unexpected GET ${path}`)
    })
    mockedApiPost.mockResolvedValue({ version: 4 })

    renderWithProviders(<Criteria />)

    const summary = await screen.findByLabelText("What you're looking for")
    await user.clear(summary)
    await user.type(summary, "Updated summary")
    await user.click(screen.getByRole("button", { name: "Save new version" }))

    expect(mockedApiPost).toHaveBeenCalledWith(
      "/api/criteria",
      expect.objectContaining({ profile_summary: "Updated summary" })
    )
    expect(await screen.findByText(/Saved as version 4/)).toBeInTheDocument()
  })

  it("shows the describe-first entry when no criteria exist", async () => {
    mockedApi.mockImplementation(async (path) => {
      if (path === "/api/criteria")
        return { current: null, total_versions: 0 }
      throw new Error(`unexpected GET ${path}`)
    })

    renderWithProviders(<Criteria />)

    expect(await screen.findByLabelText("Your description")).toBeInTheDocument()
    expect(
      screen.getByRole("heading", { name: "Describe what you're looking for" })
    ).toBeInTheDocument()
  })

  it("does not present setup when saved criteria fail to load", async () => {
    mockedApi.mockRejectedValue(new Error("offline"))

    renderWithProviders(<Criteria />)

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your criteria are still saved"
    )
    expect(
      screen.queryByRole("heading", { name: "Describe what you're looking for" })
    ).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument()
  })
})
