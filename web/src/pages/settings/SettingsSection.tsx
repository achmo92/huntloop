import { useEffect, useState, type ReactNode } from "react"
import {
  useForm,
  type DefaultValues,
  type FieldValues,
  type UseFormReturn,
} from "react-hook-form"
import { CheckIcon } from "lucide-react"
import { ApiError } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

/**
 * D-15: each settings section is its own independently-saveable unit. The
 * wrapper owns the form, the dirty gate, the save button and the two feedback
 * channels: a clear "Saved" confirmation, or the API's own 422 sentence shown
 * inline next to the fields it rejected. There is deliberately no "Save all".
 */

interface SettingsSectionProps<T extends FieldValues> {
  title: string
  description: string
  defaultValues: DefaultValues<T>
  onSave: (values: T) => Promise<void>
  note?: string
  children: (form: UseFormReturn<T>) => ReactNode
}

export function SettingsSection<T extends FieldValues>({
  title,
  description,
  defaultValues,
  onSave,
  note,
  children,
}: SettingsSectionProps<T>) {
  const form = useForm<T>({ defaultValues })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Keep the form in step with the server's values when they change (e.g. the
  // PUT response lands). defaultValues is memoized by the caller, so typing
  // never triggers this.
  useEffect(() => {
    form.reset(defaultValues)
  }, [defaultValues, form])

  async function submit(values: T) {
    setError(null)
    setSaving(true)
    try {
      await onSave(values)
      form.reset(values)
      setSaved(true)
    } catch (err) {
      setSaved(false)
      setError(
        err instanceof ApiError ? err.detail : "Couldn't save. Try again."
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <form onSubmit={form.handleSubmit(submit)}>
        <CardHeader>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5">
          <div className="grid gap-4">{children(form)}</div>
          {error ? (
            <p role="alert" className="text-sm text-destructive text-pretty">
              {error}
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-3 border-t border-border/70 pt-4">
            <Button
              type="submit"
              disabled={!form.formState.isDirty || saving}
            >
              {saving ? "Saving…" : `Save ${title}`}
            </Button>
            {note ? (
              <span className="text-xs text-muted-foreground">{note}</span>
            ) : null}
            {saved && !form.formState.isDirty ? (
              <span
                role="status"
                className="inline-flex items-center gap-1 text-sm font-medium text-success"
              >
                <CheckIcon aria-hidden="true" className="size-3.5" />
                Saved
              </span>
            ) : null}
          </div>
        </CardContent>
      </form>
    </Card>
  )
}
