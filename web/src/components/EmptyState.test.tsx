import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"
import { EmptyState } from "./EmptyState"

describe("EmptyState", () => {
  it("renders title and description", () => {
    render(
      <EmptyState title="Nothing here" description="Run discovery to find roles." />
    )
    expect(
      screen.getByRole("heading", { name: "Nothing here" })
    ).toBeInTheDocument()
    expect(screen.getByText("Run discovery to find roles.")).toBeInTheDocument()
  })

  it("renders an action button and fires onAction on click", async () => {
    const onAction = vi.fn()
    const user = userEvent.setup()
    render(
      <EmptyState
        title="Nothing here"
        description="Run discovery to find roles."
        actionLabel="Run discovery"
        onAction={onAction}
      />
    )
    await user.click(screen.getByRole("button", { name: "Run discovery" }))
    expect(onAction).toHaveBeenCalledTimes(1)
  })

  it("renders no button when actionLabel is absent", () => {
    render(
      <EmptyState title="Nothing here" description="Run discovery to find roles." />
    )
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })
})