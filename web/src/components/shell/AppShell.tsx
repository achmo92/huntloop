import type { ReactNode } from "react"
import { Outlet } from "react-router-dom"
import { AppHeader } from "@/components/shell/AppHeader"
import { AppSidebar } from "@/components/shell/AppSidebar"

interface AppShellProps {
  /**
   * A page-provided primary action. The shell accepts it now so a page can hand
   * one over later without any change to routes or behavior; today it is unused.
   */
  primaryActions?: ReactNode
}

/**
 * The app frame (GAP-17.1): a fixed sidebar for wide viewports, a sticky header,
 * and the routed page inside `<main>`. DOM order is sidebar -> header -> main so
 * focus order agrees with what the eye sees. The narrow-viewport drawer and the
 * collapsible rail are layered on in the shell-adaptation pass.
 */
export function AppShell({ primaryActions }: AppShellProps) {
  return (
    <div className="min-h-svh bg-background text-foreground">
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-64 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground lg:flex">
        <AppSidebar />
      </aside>

      <div className="flex min-h-svh flex-col lg:pl-64">
        <AppHeader primaryActions={primaryActions} onMenuClick={() => {}} />
        <main id="main" className="min-w-0 flex-1 px-6 py-8 md:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
