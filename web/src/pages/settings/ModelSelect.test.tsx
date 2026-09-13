import { useState } from "react"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"
import { ModelSelect } from "./ModelSelect"

/**
 * GAP-5: the model picker is a dropdown over the provider's own list, keeps a
 * saved value the provider omitted selectable, and keeps a free-text escape
 * hatch for custom endpoints.
 */

function Harness({
  initial = "m-a",
  models,
  fallback = false,
}: {
  initial?: string
  models: string[]
  fallback?: boolean
}) {
  const [value, setValue] = useState(initial)
  return (
    <ModelSelect
      id="model-triage"
      label="Triage model"
      value={value}
      onChange={setValue}
      models={models}
      fallback={fallback}
    />
  )
}

describe("ModelSelect", () => {
  it("renders the fetched models as options", async () => {
    const user = userEvent.setup()
    render(<Harness models={["m-a", "m-b"]} />)

    await user.click(screen.getByRole("combobox", { name: "Triage model" }))

    expect(await screen.findByRole("option", { name: "m-a" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "m-b" })).toBeInTheDocument()
  })

  it("keeps a saved model absent from the fetched list selectable and selected", async () => {
    const user = userEvent.setup()
    render(<Harness initial="legacy-7b" models={["m-a", "m-b"]} />)

    // The saved value is reflected, not silently dropped.
    expect(
      screen.getByRole("combobox", { name: "Triage model" })
    ).toHaveTextContent("legacy-7b")

    await user.click(screen.getByRole("combobox", { name: "Triage model" }))
    expect(
      await screen.findByRole("option", { name: "legacy-7b" })
    ).toBeInTheDocument()
  })

  it("reveals a free-text input when Custom model… is picked (escape hatch)", async () => {
    const user = userEvent.setup()
    render(<Harness models={["m-a", "m-b"]} />)

    await user.click(screen.getByRole("combobox", { name: "Triage model" }))
    await user.click(await screen.findByRole("option", { name: "Custom model…" }))

    const input = await screen.findByRole("textbox", { name: "Triage model" })
    await user.clear(input)
    await user.type(input, "my-own-model")

    expect(input).toHaveValue("my-own-model")
  })

  it("shows the fallback hint under the control when the provider is unreachable", () => {
    render(<Harness models={["m-a"]} fallback />)

    expect(screen.getByText(/Couldn't reach your provider/)).toBeInTheDocument()
  })
})
