import type { ReactElement } from "react"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, apiPost } from "@/lib/api"
import Onboarding from "../Onboarding"

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

function renderOnboarding(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe("Onboarding", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("describes once and prefills the editable form from the response", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue({ current: null, total_versions: 0 })
    mockedApiPost.mockResolvedValueOnce({ suggested: SUGGESTED })
    renderOnboarding(<Onboarding />)

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

  it("shows an inline currency error and blocks submit", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue(EXISTING_RESPONSE)
    renderOnboarding(<Onboarding />)

    const currency = await screen.findByLabelText("Currency")
    await user.clear(currency)
    await user.type(currency, "ZZ")
    await user.click(screen.getByRole("button", { name: "Save and continue" }))

    expect(
      await screen.findByText("Use a 3-letter code like USD or EUR")
    ).toBeInTheDocument()
    expect(mockedApiPost).not.toHaveBeenCalled()
  })

  it("saves via POST /api/criteria and shows the new version", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue(EXISTING_RESPONSE)
    mockedApiPost.mockResolvedValueOnce({ version: 2 })
    renderOnboarding(<Onboarding />)

    await screen.findByLabelText("Currency")
    await user.click(screen.getByRole("button", { name: "Save and continue" }))

    expect(await screen.findByText(/Saved as version 2/)).toBeInTheDocument()
    expect(mockedApiPost).toHaveBeenCalledWith(
      "/api/criteria",
      expect.objectContaining({
        profile_summary: EXISTING_PAYLOAD.profile_summary,
      })
    )
  })
})
