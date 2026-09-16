import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"
import { FeedbackForm } from "./FeedbackForm"

/**
 * FeedbackForm owns only its own text/submitting/confirmed/failed state — it
 * calls `onSubmit` and nothing else, so the page (plan 05-07) remains the one
 * place that knows about `/api/feedback`. That keeps this suite free of any
 * network mock: resolved and rejected promises are all the harness needs.
 */

describe("FeedbackForm", () => {
  it("refuses to submit while the box is empty or only whitespace (D-07)", async () => {
    const user = userEvent.setup()
    render(<FeedbackForm onSubmit={vi.fn()} />)

    const send = screen.getByRole("button", { name: "Send feedback" })
    expect(send).toBeDisabled()

    const box = screen.getByRole("textbox", { name: "Feedback" })
    await user.type(box, "   ")
    expect(send).toBeDisabled()
  })

  it("enables the action once there is real text", async () => {
    const user = userEvent.setup()
    render(<FeedbackForm onSubmit={vi.fn()} />)

    await user.type(
      screen.getByRole("textbox", { name: "Feedback" }),
      "Stop showing me agency reposts."
    )

    expect(
      screen.getByRole("button", { name: "Send feedback" })
    ).toBeEnabled()
  })

  it("submits the trimmed text exactly once", async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<FeedbackForm onSubmit={onSubmit} />)

    await user.type(
      screen.getByRole("textbox", { name: "Feedback" }),
      "  Weight trajectory higher.  "
    )
    await user.click(screen.getByRole("button", { name: "Send feedback" }))

    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith("Weight trajectory higher.")
  })

  it("confirms with the honest, non-instant expectation (D-08)", async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<FeedbackForm onSubmit={onSubmit} />)

    await user.type(
      screen.getByRole("textbox", { name: "Feedback" }),
      "Too many agency reposts."
    )
    await user.click(screen.getByRole("button", { name: "Send feedback" }))

    expect(
      await screen.findByText(
        "Thanks — this will be considered next time proposals are generated."
      )
    ).toBeInTheDocument()
  })

  it("clears the box after a successful send", async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<FeedbackForm onSubmit={onSubmit} />)

    const box = screen.getByRole("textbox", { name: "Feedback" })
    await user.type(box, "Too many agency reposts.")
    await user.click(screen.getByRole("button", { name: "Send feedback" }))

    expect(await screen.findByText(/Thanks —/)).toBeInTheDocument()
    expect(box).toHaveValue("")
  })

  it("keeps what the user wrote when the send fails", async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockRejectedValue(new Error("500"))
    render(<FeedbackForm onSubmit={onSubmit} />)

    const box = screen.getByRole("textbox", { name: "Feedback" })
    await user.type(box, "Too many agency reposts.")
    await user.click(screen.getByRole("button", { name: "Send feedback" }))

    expect(
      await screen.findByText("That didn't save. Try again.")
    ).toBeInTheDocument()
    expect(box).toHaveValue("Too many agency reposts.")
  })

  it("locks both controls while the send is in flight", async () => {
    const user = userEvent.setup()
    let release!: () => void
    const onSubmit = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve
        })
    )
    render(<FeedbackForm onSubmit={onSubmit} />)

    const box = screen.getByRole("textbox", { name: "Feedback" })
    await user.type(box, "Too many agency reposts.")
    await user.click(screen.getByRole("button", { name: "Send feedback" }))

    expect(box).toBeDisabled()
    expect(screen.getByRole("button", { name: "Send feedback" })).toBeDisabled()

    release()
    expect(
      await screen.findByText(/Thanks —/)
    ).toBeInTheDocument()
  })

  it("attaches to no listing — it is the general, unattached feedback box (D-07)", () => {
    render(<FeedbackForm onSubmit={vi.fn()} />)

    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
  })
})
