import { EmptyState } from "@/components/EmptyState"

export default function Dashboard() {
  return (
    <EmptyState
      title="Dashboard"
      description="Pipeline overview lands here"
      actionLabel="Run discovery"
    />
  )
}