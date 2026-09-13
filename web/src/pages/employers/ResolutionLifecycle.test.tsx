import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, apiPatch, apiPost } from "@/lib/api"
import { RegistryTable } from "./RegistryTable"
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

const FAILURE_SENTENCE =
  "We couldn't find a supported job board for this employer automatically. You can retry, or add the board details."

function company(overrides: Partial<CompanyOut>): CompanyOut {
  return {
    id: "1",
    name: "Acme",
    ats: null,
    ats_identifier: null,
    careers_url: null,
    enabled: true,
    resolved: false,
    resolution_state: "added",
    resolution_detail: null,
    possibly_stale: false,
    staleness_message: null,
    last_job_count: null,
    last_checked_at: null,
    consecutive_empty_runs: 0,
    ...overrides,
  }
}

function renderRegistry(rows: CompanyOut[]) {
  mockedApi.mockResolvedValue(rows)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <RegistryTable />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe("resolution lifecycle badges (GAP-13)", () => {
  beforeEach(() => vi.clearAllMocks())

  it("renders a neutral Added badge for a never-probed employer with no Retry", async () => {
    renderRegistry([company({ id: "1", name: "Acme", resolution_state: "added" })])

    expect(await screen.findByText("Added")).toBeInTheDocument()
    expect(screen.queryByText("Resolving…")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /retry resolution/i })
    ).not.toBeInTheDocument()
  })

  it("renders an animated Resolving… badge while a probe is in flight with no Retry", async () => {
    renderRegistry([
      company({ id: "2", name: "Globex", resolution_state: "resolving" }),
    ])

    expect(await screen.findByText("Resolving…")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /retry resolution/i })
    ).not.toBeInTheDocument()
  })

  it("renders a success badge with platform and board id when resolved with no Retry", async () => {
    renderRegistry([
      company({
        id: "3",
        name: "Acme",
        ats: "greenhouse",
        ats_identifier: "acme",
        resolved: true,
        resolution_state: "resolved",
      }),
    ])

    expect(await screen.findByText("Greenhouse · acme")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /retry resolution/i })
    ).not.toBeInTheDocument()
  })

  it("renders a destructive Needs attention badge with the generic sentence and a Retry only in Error", async () => {
    renderRegistry([
      company({
        id: "4",
        name: "Initech",
        resolution_state: "error",
        resolution_detail: FAILURE_SENTENCE,
      }),
    ])

    expect(await screen.findByText("Needs attention")).toBeInTheDocument()
    expect(screen.getByText(FAILURE_SENTENCE)).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Retry resolution for Initech" })
    ).toBeInTheDocument()
  })
})
