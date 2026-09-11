import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"

interface DetailDrawerProps {
  jobId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * Task 1 plumbing: the table's row click opens a side sheet (D-09). The full
 * breakdown/reasoning/facts/notes/timeline content lands in Task 2.
 */
export function DetailDrawer({ jobId, open, onOpenChange }: DetailDrawerProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>Listing detail</SheetTitle>
        </SheetHeader>
        <div className="overflow-y-auto px-4 pb-4 text-sm text-muted-foreground">
          {jobId ? "Loading listing…" : null}
        </div>
      </SheetContent>
    </Sheet>
  )
}
