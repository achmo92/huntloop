import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, ApiError, apiPost } from "@/lib/api"
import { CoverageCard } from "./CoverageCard"
import type { CompanyOut, CoverageOut } from "./types"

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

const GLOBEX: CompanyOut = {
  id: "1",
  name: "Globex",
  ats: null,
  ats_identifier: null,
  careers_url: null,
  enabled: true,
  resolved: false,
  resolution_state: "error",
  resolution_detail: "No job board found",
  possibly_stale: false,
  staleness_message: null,
  last_job_count: null,
  last_checked_at: null,
  consecutive_empty_runs: 0,
}

const ACME: CompanyOut = {
  id: "2",
  name: "Acme",
  ats: "greenhouse",
  ats_identifier: "acme",
  careers_url: null,
  enabled: true,
  resolved: true,
  resolution_state: "resolved",
  resolution_detail: null,
  possibly_stale: false,
  staleness_message: null,
  last_job_count: 5,
  last_checked_at: "2026-09-10T08:00:00Z",
  consecutive_empty_runs: 0,
}

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

function renderCard(client: QueryClient) {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CoverageCard />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

function mockCoverage(companies: CompanyOut[], coverage: CoverageOut) {
  mockedApi.mockImplementation(async (path) => {
    if (path === "/api/companies/coverage") return coverage
    if (path === "/api/companies") return companies
    throw new Error(`unexpected GET ${path}`)
  })
}

describe("CoverageCard retry action (GAP-7)", () => {
  beforeEach(() => vi.clearAllMocks())

  it("renders a retry action button and no navigation link when employers need attention", async () => {
    mockCoverage([GLOBEX, ACME], {
      added: 3,
      watchable: 2,
      needs_attention: 1,
      resolved: 2,
    })
    renderCard(makeClient())

    expect(
      await screen.findByRole("button", { name: /retry all/i })
    ).toBeInTheDocument()
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
  })

  it("posts only the unresolved employer ids to resolve-batch", async () => {
    const user = userEvent.setup()
    mockCoverage([GLOBEX, ACME], {
      added: 3,
      watchable: 2,
      needs_attention: 1,
      resolved: 2,
    })
    mockedApiPost.mockResolvedValue(undefined)
    renderCard(makeClient())

    await user.click(await screen.findByRole("button", { name: /retry all/i }))

    expect(mockedApiPost).toHaveBeenCalledWith(
      "/api/companies/resolve-batch",
      { ids: ["1"] }
    )
  })

  it("excludes an employer already resolving from the retry-all list", async () => {
    const user = userEvent.setup()
    const resolving: CompanyOut = {
      ...GLOBEX,
      id: "3",
      name: "Hooli",
      resolution_state: "resolving",
    }
    mockCoverage([GLOBEX, resolving, ACME], {
      added: 3,
      watchable: 1,
      needs_attention: 2,
      resolved: 1,
    })
    mockedApiPost.mockResolvedValue(undefined)
    renderCard(makeClient())

    await user.click(await screen.findByRole("button", { name: /retry all/i }))

    expect(mockedApiPost).toHaveBeenCalledWith(
      "/api/companies/resolve-batch",
      { ids: ["1"] }
    )
  })

  it("shows an in-progress, disabled state while the batch request is pending", async () => {
    const user = userEvent.setup()
    mockCoverage([GLOBEX], {
      added: 3,
      watchable: 2,
      needs_attention: 1,
      resolved: 2,
    })
    mockedApiPost.mockReturnValue(new Promise(() => {}))
    renderCard(makeClient())

    await user.click(await screen.findByRole("button", { name: /retry all/i }))

    const pending = screen.getByRole("button", { name: /retrying/i })
    expect(pending).toBeDisabled()
    expect(pending).toHaveTextContent("Retrying…")
  })

  it("surfaces a failed batch start as a non-blocking status and returns to idle", async () => {
    const user = userEvent.setup()
    mockCoverage([GLOBEX], {
      added: 3,
      watchable: 2,
      needs_attention: 1,
      resolved: 2,
    })
    mockedApiPost.mockRejectedValue(
      new ApiError(500, "Couldn't start the retry.")
    )
    renderCard(makeClient())

    await user.click(await screen.findByRole("button", { name: /retry all/i }))

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Couldn't start the retry."
    )
    expect(screen.getByRole("button", { name: /retry all/i })).toBeEnabled()
  })

  it("invalidates both the companies and coverage caches after queueing", async () => {
    const user = userEvent.setup()
    mockCoverage([GLOBEX], {
      added: 3,
      watchable: 2,
      needs_attention: 1,
      resolved: 2,
    })
    mockedApiPost.mockResolvedValue(undefined)
    const client = makeClient()
    const invalidate = vi.spyOn(client, "invalidateQueries")
    renderCard(client)

    await user.click(await screen.findByRole("button", { name: /retry all/i }))

    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["companies"] })
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["coverage"] })
    })
  })

  it("renders no retry action when nothing needs attention", async () => {
    mockCoverage([ACME], {
      added: 2,
      watchable: 2,
      needs_attention: 0,
      resolved: 2,
    })
    renderCard(makeClient())

    await screen.findByText(/watching 2 of your 2 added employers/)
    expect(
      screen.queryByRole("button", { name: /retry all/i })
    ).not.toBeInTheDocument()
  })
})
