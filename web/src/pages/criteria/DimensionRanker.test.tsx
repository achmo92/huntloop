import { useState } from "react"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"
import {
  DIMENSION_KEYS,
  DimensionRanker,
  type DimensionKey,
} from "./DimensionRanker"

function Harness() {
  const [order, setOrder] = useState<DimensionKey[]>([...DIMENSION_KEYS])
  return <DimensionRanker value={order} onChange={setOrder} />
}

describe("DimensionRanker", () => {
  it("maps top-to-bottom position to weights 4/3/2/1", () => {
    render(<Harness />)
    const preview = screen.getByTestId("weights-preview")
    expect(preview).toHaveTextContent("Role fit weight 4")
    expect(preview).toHaveTextContent("Seniority fit weight 3")
    expect(preview).toHaveTextContent("Employer fit weight 2")
    expect(preview).toHaveTextContent("Trajectory weight 1")
  })

  it("reorders with the arrow fallback and updates the weights preview", async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(screen.getByRole("button", { name: "Move Seniority fit up" }))

    const preview = screen.getByTestId("weights-preview")
    expect(preview).toHaveTextContent("Seniority fit weight 4")
    expect(preview).toHaveTextContent("Role fit weight 3")
  })
})
