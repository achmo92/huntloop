import type { ReactElement } from "react"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { ApiError, api, apiPost } from "@/lib/api"
import ProposalsPage from "./ProposalsPage"
import type { ProposalOut } from "./types"

/**
 * UI-03's named verification command in 05-VALIDATION.md
 * (`npx vitest run src/pages/proposals/ProposalsPage.test.tsx`).
 *
 * The page is the one place the four endpoints are wired (plan 05-04's
 * `GET /api/proposals`, `POST /api/proposals/{id}/accept`,
 * `POST /api/proposals/{id}/reject`, `POST /api/feedback`). ProposalCard and
 * FeedbackForm are deliberately not mocked: proving the real composition wires
 * the real endpoints is the point of this file.
 */

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

const PENDING_QUERY = "/api/proposals?status=pending&limit=50"

const EMPTY_BODY =
  "Once discovery has run a few times and listings have moved through your pipeline, HuntLoop will suggest evidence-backed changes here — nothing changes until you approve it."

function proposal(
  overrides: Partial<ProposalOut> & { id: string }
): ProposalOut {
  return {
    created_at: "2026-09-14T09:00:00Z",
    status: "pending",
    based_on_run_id: "run-1",
    proposed_changes: {
      field: "seniority_min",
      direction: "tighten",
      current_value: "mid",
      proposed_value: "senior",
    },
    rationale: "Listings above your ceiling were rejected quickly.",
    evidence: null,
    predicted_effect: null,
    decided_at: null,
    resulting_version: null,
    rejection_reason: null,
    prior_rejections: [],
    ...overrides,
  }
}

const SENIORITY = proposal({ id: "prop-seniority" })
const POSTING_AGE = proposal({
  id: "prop-age",
  proposed_changes: {
    field: "posting_age_days",
    direction: "tighten",
    current_value: 30,
    proposed_value: 21,
  },
})

function renderPage(ui: ReactElement = <ProposalsPage />) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

function mockProposals(rows: ProposalOut[]) {
  mockedApi.mockImplementation(async (path) => {
    if (path === PENDING_QUERY) return rows
    throw new Error(`unexpected GET ${path}`)
  })
}

describe("ProposalsPage (UI-03)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiPost.mockReset()
  })

  it("renders a loading skeleton with no cards while the list is pending", () => {
    mockedApi.mockImplementation(() => new Promise<ProposalOut[]>(() => {}))
    renderPage()

    expect(
      document.querySelector('[data-slot="skeleton-table"]')
    ).not.toBeNull()
    expect(screen.getByRole("status")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Approve change" })
    ).not.toBeInTheDocument()
  })

  it("renders one card per pending proposal with its field label", async () => {
    mockProposals([SENIORITY, POSTING_AGE])
    renderPage()

    expect(
      await screen.findByText("Minimum seniority level")
    ).toBeInTheDocument()
    expect(screen.getByText("Posting age limit")).toBeInTheDocument()
    expect(
      screen.getAllByRole("button", { name: "Approve change" })
    ).toHaveLength(2)
  })

  it("fetches only pending proposals", async () => {
    mockProposals([])
    renderPage()

    await screen.findByText("No proposals yet")
    expect(mockedApi).toHaveBeenCalledWith(
      expect.stringContaining("/api/proposals")
    )
    expect(mockedApi).toHaveBeenCalledWith(
      expect.stringContaining("status=pending")
    )
  })

  it("explains the empty state instead of showing an empty list", async () => {
    mockProposals([])
    renderPage()

    const heading = await screen.findByText("No proposals yet")
    expect(screen.getByText(EMPTY_BODY)).toBeInTheDocument()
    // No action button: there is nothing productive to click before data exists.
    const emptyState = heading.closest("div") as HTMLElement
    expect(within(emptyState).queryByRole("button")).not.toBeInTheDocument()
  })

  it("shows the approved list-load error when the query fails", async () => {
    mockedApi.mockRejectedValue(new Error("500"))
    renderPage()

    expect(
      await screen.findByText(
        "We couldn't load your proposals. Refresh to try again."
      )
    ).toBeInTheDocument()
  })

  it("approves a proposal and refetches the list", async () => {
    const user = userEvent.setup()
    mockProposals([SENIORITY])
    mockedApiPost.mockResolvedValue({ version: 2 })
    renderPage()

    await screen.findByText("Minimum seniority level")
    await user.click(screen.getByRole("button", { name: "Approve change" }))

    expect(mockedApiPost).toHaveBeenCalledWith(
      "/api/proposals/prop-seniority/accept",
      {}
    )
    await waitFor(() =>
      expect(mockedApi.mock.calls.length).toBeGreaterThanOrEqual(2)
    )
  })

  it("rejects a proposal with the typed reason", async () => {
    const user = userEvent.setup()
    mockProposals([SENIORITY])
    mockedApiPost.mockResolvedValue({ status: "rejected" })
    renderPage()

    await screen.findByText("Minimum seniority level")
    await user.type(
      screen.getByRole("textbox", { name: "Reason (optional)" }),
      "Too senior"
    )
    await user.click(screen.getByRole("button", { name: "Reject change" }))

    expect(mockedApiPost).toHaveBeenCalledWith(
      "/api/proposals/prop-seniority/reject",
      { reason: "Too senior" }
    )
  })

  it("rejects without a reason by sending null", async () => {
    const user = userEvent.setup()
    mockProposals([SENIORITY])
    mockedApiPost.mockResolvedValue({ status: "rejected" })
    renderPage()

    await screen.findByText("Minimum seniority level")
    await user.click(screen.getByRole("button", { name: "Reject change" }))

    expect(mockedApiPost).toHaveBeenCalledWith(
      "/api/proposals/prop-seniority/reject",
      { reason: null }
    )
  })

  it("scopes an action error to the card that failed", async () => {
    const user = userEvent.setup()
    mockProposals([SENIORITY, POSTING_AGE])
    mockedApiPost.mockRejectedValue(new ApiError(500, "failed"))
    renderPage()

    await screen.findByText("Minimum seniority level")
    await user.click(
      screen.getAllByRole("button", { name: "Approve change" })[0]
    )

    const alerts = await screen.findAllByRole("alert")
    expect(alerts).toHaveLength(1)
    const failedCard = screen
      .getByText("Minimum seniority level")
      .closest('[data-slot="card"]') as HTMLElement
    expect(within(failedCard).getByRole("alert")).toBeInTheDocument()
    const sibling = screen
      .getByText("Posting age limit")
      .closest('[data-slot="card"]') as HTMLElement
    expect(within(sibling).queryByRole("alert")).not.toBeInTheDocument()
  })

  it("refetches when a proposal was already decided elsewhere (409)", async () => {
    const user = userEvent.setup()
    mockProposals([SENIORITY])
    mockedApiPost.mockRejectedValue(new ApiError(409, "already decided"))
    renderPage()

    await screen.findByText("Minimum seniority level")
    await user.click(screen.getByRole("button", { name: "Approve change" }))

    await waitFor(() =>
      expect(mockedApi.mock.calls.length).toBeGreaterThanOrEqual(2)
    )
  })

  it("disables only the acting card while the request is in flight", async () => {
    const user = userEvent.setup()
    mockProposals([SENIORITY, POSTING_AGE])
    let release!: (value: unknown) => void
    mockedApiPost.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve
        })
    )
    renderPage()

    await screen.findByText("Minimum seniority level")
    await user.click(
      screen.getAllByRole("button", { name: "Approve change" })[0]
    )

    const approveButtons = screen.getAllByRole("button", {
      name: "Approve change",
    })
    expect(approveButtons[0]).toBeDisabled()
    expect(approveButtons[1]).toBeEnabled()

    release({ version: 2 })
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Approve change" })[0]
      ).toBeEnabled()
    )
  })

  it("posts the feedback box to the feedback endpoint", async () => {
    const user = userEvent.setup()
    mockProposals([SENIORITY])
    mockedApiPost.mockResolvedValue({ id: "note-1" })
    renderPage()

    await screen.findByText("Minimum seniority level")
    await user.type(
      screen.getByRole("textbox", { name: "Feedback" }),
      "Fewer agency reposts"
    )
    await user.click(screen.getByRole("button", { name: "Send feedback" }))

    expect(mockedApiPost).toHaveBeenCalledWith("/api/feedback", {
      text: "Fewer agency reposts",
    })
  })

  it("confirms feedback with the approved sentence", async () => {
    const user = userEvent.setup()
    mockProposals([SENIORITY])
    mockedApiPost.mockResolvedValue({ id: "note-1" })
    renderPage()

    await screen.findByText("Minimum seniority level")
    await user.type(
      screen.getByRole("textbox", { name: "Feedback" }),
      "Fewer agency reposts"
    )
    await user.click(screen.getByRole("button", { name: "Send feedback" }))

    expect(
      await screen.findByText(
        "Thanks — this will be considered next time proposals are generated."
      )
    ).toBeInTheDocument()
  })

  it("keeps the proposals visible when the feedback post fails", async () => {
    const user = userEvent.setup()
    mockProposals([SENIORITY])
    mockedApiPost.mockRejectedValue(new Error("500"))
    renderPage()

    await screen.findByText("Minimum seniority level")
    await user.type(
      screen.getByRole("textbox", { name: "Feedback" }),
      "Fewer agency reposts"
    )
    await user.click(screen.getByRole("button", { name: "Send feedback" }))

    expect(
      await screen.findByText("That didn't save. Try again.")
    ).toBeInTheDocument()
    expect(screen.getByText("Minimum seniority level")).toBeInTheDocument()
  })

  it("carries the feedback box on the same page as the proposals (D-07/D-11)", async () => {
    mockProposals([SENIORITY])
    renderPage()

    expect(
      await screen.findByText("Minimum seniority level")
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Send feedback" })
    ).toBeInTheDocument()
  })
})
