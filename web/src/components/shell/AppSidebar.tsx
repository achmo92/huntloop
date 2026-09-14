import { NavLink } from "react-router-dom"
import { PanelLeftCloseIcon, PanelLeftOpenIcon } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { BrandMark } from "@/components/shell/BrandMark"
import { NAV_ITEMS } from "@/components/shell/nav"

interface AppSidebarProps {
  /** Render the icon rail: labels become visually hidden, names stay intact. */
  collapsed?: boolean
  /** When provided, a fold control is rendered. */
  onToggleCollapsed?: () => void
  /** Called after a section is chosen (the mobile drawer closes on it). */
  onNavigate?: () => void
}

/**
 * The primary navigation (GAP-17.1). Each entry carries an icon *and* a label,
 * and the active route is signalled three ways — an accent rail, an accent
 * surface and a weight increase — so it never relies on colour alone. The
 * `NavLink` sets `aria-current="page"` itself; nothing overrides it.
 */
export function AppSidebar({
  collapsed = false,
  onToggleCollapsed,
  onNavigate,
}: AppSidebarProps) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div
        className={cn(
          "flex h-16 shrink-0 items-center",
          collapsed ? "justify-center px-2" : "px-5"
        )}
      >
        <BrandMark markOnly={collapsed} />
      </div>

      <Separator className="bg-sidebar-border" />

      <nav
        aria-label="Primary"
        data-collapsed={collapsed}
        className={cn(
          "flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto p-3",
          collapsed && "items-stretch px-2"
        )}
      >
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={onNavigate}
              title={collapsed ? item.label : undefined}
              className={({ isActive }) =>
                cn(
                  "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                  collapsed && "justify-center px-0 py-2.5",
                  isActive
                    ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                    : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                )
              }
            >
              {({ isActive }) => (
                <>
                  <span
                    aria-hidden="true"
                    className={cn(
                      "absolute top-1/2 left-0 h-5 w-[3px] -translate-y-1/2 rounded-full bg-brand transition-opacity",
                      isActive ? "opacity-100" : "opacity-0"
                    )}
                  />
                  <Icon aria-hidden="true" className="size-4 shrink-0" />
                  <span className={cn("truncate", collapsed && "sr-only")}>
                    {item.label}
                  </span>
                </>
              )}
            </NavLink>
          )
        })}
      </nav>

      {onToggleCollapsed ? (
        <div
          className={cn(
            "shrink-0 border-t border-sidebar-border p-3",
            collapsed && "flex justify-center px-2"
          )}
        >
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={onToggleCollapsed}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="text-muted-foreground hover:text-foreground"
          >
            {collapsed ? (
              <PanelLeftOpenIcon aria-hidden="true" />
            ) : (
              <PanelLeftCloseIcon aria-hidden="true" />
            )}
          </Button>
        </div>
      ) : null}
    </div>
  )
}
