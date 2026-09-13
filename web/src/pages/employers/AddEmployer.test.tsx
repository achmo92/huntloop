import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, ApiError, apiPost } from "@/lib/api"
import Employers from "../Employers"
import { RESOLVE_TIMEOUT_MS } from "./AddEmployerDialog"
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

const HOOLI_CREATED: CompanyOut = {
  id: "9",
  name: "Hooli",
  ats: null,
  ats_identifier: null,
  careers_url: null,
  enabled: true,
  resolved: false,
  resolution_status: "needs_attention",
  resolution_detail: null,
  possibly_stale: false,
  staleness_message: null,
  last_job_count: null,
  last_checked_at: null,
  consecutive_empty_runs: 0,
}

const HOOLI_RESOLVED: CompanyOut = {
  ...HOOLI_CREATED,
  ats: "greenhouse",
  ats_identifier: "hooli",
  careers_url: null,
  resolved: true,
  resolution_status: "resolved",
  last_job_count: 4,
  last_checked_at: "2026-09-13T10:00:00Z",
}

function renderWithProviders(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    await screen.findByRole("button", { name: "Add employer" })
  )
  return screen.findByRole("dialog")
}

describe("Add employer dialog (GAP-8)", () => {
  beforeEach(() => vi.clearAllMocks())

  it("opens a dialog with a name field and an optional careers URL field", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue([])
    renderWithProviders(<Employers />)

    expect(await openDialog(user)).toBeInTheDocument()
    expect(screen.getByLabelText("Name")).toBeInTheDocument()
    expect(screen.getByLabelText(/careers url/i)).toBeInTheDocument()
  })

  it("adds then auto-resolves: POST /api/companies then /{id}/resolve, with an in-progress label", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue([])
    mockedApiPost.mockImplementation(async (path) => {
      if (path === "/api/companies") return HOOLI_CREATED
      if (path === "/api/companies/9/resolve") return new Promise(() => {})
      throw new Error(`unexpected POST ${path}`)
    })
    renderWithProviders(<Employers />)
    await openDialog(user)

    await user.type(screen.getByLabelText("Name"), "Hooli")
    await user.click(screen.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(mockedApiPost).toHaveBeenCalledWith("/api/companies", {
        name: "Hooli",
        careers_url: null,
      })
    )
    expect(mockedApiPost).toHaveBeenCalledWith(
      "/api/companies/9/resolve",
      {}
    )
    expect(
      await screen.findByText("Finding their job board…")
    ).toBeInTheDocument()
  })

  it("closes once the poll reports the new employer resolved, and lands it in the registry", async () => {
    const user = userEvent.setup()
    let listings: CompanyOut[] = []
    mockedApi.mockImplementation(async (path) => {
      if (path === "/api/companies") return listings
      if (path === "/api/companies/coverage")
        return { added: 1, watchable: 1, needs_attention: 0, resolved: 1 }
      throw new Error(`unexpected GET ${path}`)
    })
    mockedApiPost.mockImplementation(async (path) => {
      if (path === "/api/companies") return HOOLI_CREATED
      if (path === "/api/companies/9/resolve") {
        listings = [HOOLI_RESOLVED]
        return { status: "accepted" }
      }
      throw new Error(`unexpected POST ${path}`)
    })
    renderWithProviders(<Employers />)
    await openDialog(user)

    await user.type(screen.getByLabelText("Name"), "Hooli")
    await user.click(screen.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    )
    expect(await screen.findByText("Hooli")).toBeInTheDocument()
  })

  it("blocks an empty name inline without calling the API", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue([])
    renderWithProviders(<Employers />)
    await openDialog(user)

    await user.type(screen.getByLabelText("Name"), "   ")
    await user.click(screen.getByRole("button", { name: "Add" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a name")
    expect(mockedApiPost).not.toHaveBeenCalled()
  })

  it("keeps the dialog open with the API error and re-enables submit when create fails", async () => {
    const user = userEvent.setup()
    mockedApi.mockResolvedValue([])
    mockedApiPost.mockRejectedValue(new ApiError(422, "Enter a valid name"))
    renderWithProviders(<Employers />)
    await openDialog(user)

    await user.type(screen.getByLabelText("Name"), "Hooli")
    await user.click(screen.getByRole("button", { name: "Add" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a valid name"
    )
    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add" })).toBeEnabled()
  })

  it("stops polling at the bounded timeout and reports it honestly", async () => {
    vi.useFakeTimers()
    try {
      mockedApi.mockResolvedValue([])
      mockedApiPost.mockImplementation(async (path) => {
        if (path === "/api/companies") return HOOLI_CREATED
        if (path === "/api/companies/9/resolve") return { status: "accepted" }
        throw new Error(`unexpected POST ${path}`)
      })
      renderWithProviders(<Employers />)

      fireEvent.click(screen.getByRole("button", { name: "Add employer" }))
      fireEvent.change(screen.getByLabelText("Name"), {
        target: { value: "Hooli" },
      })
      fireEvent.click(screen.getByRole("button", { name: "Add" }))
      await act(async () => {
        await Promise.resolve()
        await Promise.resolve()
      })

      expect(screen.getByText("Finding their job board…")).toBeInTheDocument()

      await act(async () => {
        vi.advanceTimersByTime(RESOLVE_TIMEOUT_MS)
      })

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
      expect(screen.getByRole("status")).toHaveTextContent(
        /still finding their job board/i
      )
    } finally {
      vi.useRealTimers()
    }
  })
})
