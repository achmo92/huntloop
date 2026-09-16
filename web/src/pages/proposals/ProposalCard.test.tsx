import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"
import { ProposalCard } from "./ProposalCard"
import type {
  Evidence,
  EvidenceListing,
  PredictedEffect,
  ProposalOut,
} from "./types"

/**
 * ProposalCard is presentational: it performs no I/O and owns only the
 * optional rejection reason. Every effect on the network belongs to the page
 * (plan 05-07), so these tests render bare — the same convention as
 * DimensionRanker.test.tsx — rather than wrapping in the Runs.tsx query/router
 * harness that exists there for the page's own fetching.
 *
 * `predictedEffect.ts` is deliberately NOT mocked: 05-UI-SPEC's copy is a
 * contract, and the only way to assert it is through the real render.
 */

function makeProposal(overrides: Partial<ProposalOut> = {}): ProposalOut {
  return {
    id: "proposal-1",
    created_at: "2026-09-15T09:00:00Z",
    status: "pending",
    based_on_run_id: "run-1",
    proposed_changes: {
      field: "posting_age_days",
      direction: "tighten",
      current_value: 30,
      proposed_value: 20,
    },
    rationale:
      "You rejected most senior listings within a week of seeing them.",
    evidence: null,
    predicted_effect: null,
    decided_at: null,
    resulting_version: null,
    rejection_reason: null,
    prior_rejections: [],
    ...overrides,
  }
}

const LISTINGS: EvidenceListing[] = [
  {
    job_id: "job-1",
    title: "Staff Engineer",
    company: "Acme Corp",
    status: "rejected",
    dwell_days: 3,
    transitions: [
      { from: "new", to: "rejected", changed_at: "2026-09-10T08:00:00Z" },
    ],
    attributed_to: "seniority_fit",
  },
  {
    job_id: "job-2",
    title: "Principal Engineer",
    company: "Globex",
    status: "rejected",
    dwell_days: 4,
    transitions: [],
    attributed_to: null,
  },
]

const EVIDENCE: Evidence = {
  signal: "fast_rejection",
  observation_count: 6,
  threshold: 3,
  truncated: false,
  listings: LISTINGS,
  feedback_quotes: [{ note_id: "note-1", quote: "Too many agency reposts." }],
}

function renderCard(proposal: ProposalOut, extra: Partial<Parameters<typeof ProposalCard>[0]> = {}) {
  const onApprove = vi.fn()
  const onReject = vi.fn()
  const view = render(
    <ProposalCard
      proposal={proposal}
      onApprove={onApprove}
      onReject={onReject}
      {...extra}
    />
  )
  return { ...view, onApprove, onReject }
}

describe("ProposalCard", () => {
  it("renders the humanised field label and both values (what would change)", () => {
    renderCard(makeProposal())

    expect(screen.getByText("Posting age limit")).toBeInTheDocument()
    expect(screen.getByText("30")).toBeInTheDocument()
    expect(screen.getByText("20")).toBeInTheDocument()
  })

  it("renders the rationale (why)", () => {
    renderCard(makeProposal())

    expect(
      screen.getByText(
        "You rejected most senior listings within a week of seeing them."
      )
    ).toBeInTheDocument()
  })

  it("renders each evidence listing's title and company, and flags a truncated set (LOOP-05)", () => {
    renderCard(
      makeProposal({ evidence: { ...EVIDENCE, truncated: true } })
    )

    expect(screen.getByText("Staff Engineer")).toBeInTheDocument()
    expect(screen.getByText("Acme Corp")).toBeInTheDocument()
    expect(screen.getByText("Principal Engineer")).toBeInTheDocument()
    expect(screen.getByText("Globex")).toBeInTheDocument()
    expect(
      screen.getByText("More listings matched than are shown here.")
    ).toBeInTheDocument()
  })

  it("renders the user's own feedback quotes as evidence (D-07/D-08)", () => {
    renderCard(makeProposal({ evidence: EVIDENCE }))

    expect(screen.getByText("Too many agency reposts.")).toBeInTheDocument()
  })

  it("renders the filter dry-run predicted effect verbatim (LOOP-06)", () => {
    const effect: PredictedEffect = {
      kind: "filter_dry_run",
      field: "posting_age_days",
      would_exclude: 4,
      would_include: 0,
      backlog_size: 41,
      approximate: false,
      sample: [],
    }
    renderCard(makeProposal({ predicted_effect: effect }))

    expect(
      screen.getByText("4 of your current listings would now be excluded")
    ).toBeInTheDocument()
  })

  it("says a literal-count tier is informational rather than enforced (Tier C)", () => {
    const effect: PredictedEffect = {
      kind: "literal_count",
      field: "exclusions.title_keywords",
      term: "agency",
      matches: 3,
      enforced: false,
      backlog_size: 41,
      note: "isn't enforced by discovery yet",
    }
    renderCard(makeProposal({ predicted_effect: effect }))

    expect(
      screen.getByText(/isn't enforced by discovery yet/)
    ).toBeInTheDocument()
  })

  it("shows a prior rejection with its date and stated reason (LOOP-09)", () => {
    const decidedAt = "2026-08-01T12:00:00Z"
    const expectedDate = new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
    }).format(new Date(decidedAt))
    renderCard(
      makeProposal({
        prior_rejections: [
          { decided_at: decidedAt, rejection_reason: "Too blunt" },
        ],
      })
    )

    expect(screen.getByTestId("rejection-history")).toHaveTextContent(
      `You rejected a similar proposal on ${expectedDate}`
    )
    expect(screen.getByText("Too blunt")).toBeInTheDocument()
  })

  it("renders no rejection history at all when there is none", () => {
    renderCard(makeProposal({ prior_rejections: [] }))

    expect(screen.queryByTestId("rejection-history")).not.toBeInTheDocument()
  })

  it("approves the exact proposal that was reviewed", async () => {
    const user = userEvent.setup()
    const { onApprove } = renderCard(makeProposal())

    await user.click(screen.getByRole("button", { name: "Approve change" }))

    expect(onApprove).toHaveBeenCalledTimes(1)
    expect(onApprove).toHaveBeenCalledWith("proposal-1")
  })

  it("rejects with the typed reason, or with null when the reason is empty", async () => {
    const user = userEvent.setup()
    const { onReject } = renderCard(makeProposal())

    const reason = screen.getByRole("textbox", { name: "Reason (optional)" })
    await user.type(reason, "Too blunt")
    await user.click(screen.getByRole("button", { name: "Reject change" }))

    expect(onReject).toHaveBeenCalledWith("proposal-1", "Too blunt")

    onReject.mockClear()
    await user.clear(reason)
    await user.click(screen.getByRole("button", { name: "Reject change" }))

    expect(onReject).toHaveBeenCalledWith("proposal-1", null)
  })

  it("rejects immediately, with no confirmation dialog (05-UI-SPEC)", async () => {
    const user = userEvent.setup()
    const { onReject } = renderCard(makeProposal())

    await user.click(screen.getByRole("button", { name: "Reject change" }))

    expect(onReject).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("disables both actions while a decision is in flight", () => {
    renderCard(makeProposal(), { busy: true })

    expect(screen.getByRole("button", { name: "Approve change" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Reject change" })).toBeDisabled()
  })

  it("surfaces the inline failure sentence verbatim", () => {
    renderCard(makeProposal(), { error: true })

    expect(
      screen.getByText("That didn't save. Try again.")
    ).toBeInTheDocument()
  })

  it("offers no actions once the proposal has been decided", () => {
    const accepted = renderCard(makeProposal({ status: "accepted" }))
    expect(
      screen.queryByRole("button", { name: "Approve change" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Reject change" })
    ).not.toBeInTheDocument()
    accepted.unmount()

    renderCard(makeProposal({ status: "rejected" }))
    expect(
      screen.queryByRole("button", { name: "Approve change" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Reject change" })
    ).not.toBeInTheDocument()
  })
})
