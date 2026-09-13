import type { ReactElement } from "react"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, apiPost } from "@/lib/api"
import Employers from "../Employers"
import type { CompanyOut } from "../criteria/types"

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

const GLOBEX_PENDING: CompanyOut = {
  id: "2",
  name: "Globex",
  ats: null,
  ats_identifier: null,
  careers_url: null,
  enabled: true,
  resolved: false,
  resolution_status: "needs_attention",
  resolution_detail: "We couldn't find a supported job board for this employer.",
  possibly_stale: false,
  staleness_message: null,
  last_job_count: null,
  last_checked_at: null,
  consecutive_empty_runs: 0,
}

const GLOBEX_RESOLVED: CompanyOut = {
  ...GLOBEX_PENDING,
  ats: "greenhouse",
  ats_identifier: "globex",
  resolved: true,
  resolution_status: "resolved",
  resolution_detail: null,
  last_checked_at: "2026-09-13T10:00:00Z",
}

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

const COVERAGE_PENDING = {
  added: 1,
  watchable: 0,
  needs_attention: 1,
  resolved: 0,
}

describe("Bulk retry marks registry rows (GAP-9)", () => {
  beforeEach(() => vi.clearAllMocks())

  it("disables a pending registry row and reads Retrying… while a bulk retry is in flight", async () => {
    const user = userEvent.setup()
    mockedApi.mockImplementation(async (path) => {
      if (path === "/api/companies") return [GLOBEX_PENDING]
      if (path === "/api/companies/coverage") return COVERAGE_PENDING
      throw new Error(`unexpected GET ${path}`)
    })
    mockedApiPost.mockReturnValue(new Promise(() => {}))
    renderWithProviders(<Employers />)

    await user.click(await screen.findByRole("button", { name: /retry all/i }))

    expect(mockedApiPost).toHaveBeenCalledWith(
      "/api/companies/resolve-batch",
      { ids: ["2"] }
    )
    const retry = await screen.findByRole("button", {
      name: "Retry resolution for Globex",
    })
    expect(retry).toBeDisabled()
    expect(retry).toHaveTextContent("Retrying…")
  })

  it("clears the retrying state once the queued row lands resolved", async () => {
    const user = userEvent.setup()
    let listings: CompanyOut[] = [GLOBEX_PENDING]
    let acceptBatch: (value: { status: string }) => void = () => {}
    mockedApi.mockImplementation(async (path) => {
      if (path === "/api/companies") return listings
      if (path === "/api/companies/coverage") return COVERAGE_PENDING
      throw new Error(`unexpected GET ${path}`)
    })
    mockedApiPost.mockImplementation((path) => {
      if (path === "/api/companies/resolve-batch") {
        return new Promise((resolve) => {
          acceptBatch = resolve
        })
      }
      throw new Error(`unexpected POST ${path}`)
    })
    renderWithProviders(<Employers />)

    await user.click(await screen.findByRole("button", { name: /retry all/i }))

    // The batch is queued (request pending) and the row shows the shared
    // retrying affordance.
    expect(
      await screen.findByRole("button", {
        name: "Retry resolution for Globex",
      })
    ).toHaveTextContent("Retrying…")

    // The probe lands: the queued row is now resolved, so the derived in-flight
    // state self-clears.
    listings = [GLOBEX_RESOLVED]
    await act(async () => {
      acceptBatch({ status: "accepted" })
    })

    await waitFor(() =>
      expect(
        screen.queryByRole("button", {
          name: "Retry resolution for Globex",
        })
      ).not.toBeInTheDocument()
    )
    expect(screen.queryByText("Retrying…")).not.toBeInTheDocument()
  })
})
