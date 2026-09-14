import {
  Building2Icon,
  HistoryIcon,
  LayoutDashboardIcon,
  ListIcon,
  SettingsIcon,
  SlidersHorizontalIcon,
  type LucideIcon,
} from "lucide-react"

/**
 * The single source of the six-section navigation (GAP-17.1). The sidebar and
 * the mobile drawer both map this list, so an entry can never drift between the
 * two. `context` is the one-line product-language summary the header shows.
 */
export interface NavItem {
  to: string
  label: string
  end?: boolean
  icon: LucideIcon
  context: string
}

export const NAV_ITEMS: NavItem[] = [
  {
    to: "/",
    label: "Dashboard",
    end: true,
    icon: LayoutDashboardIcon,
    context: "Your pipeline at a glance",
  },
  {
    to: "/listings",
    label: "Listings",
    icon: ListIcon,
    context: "Every role we scored for you",
  },
  {
    to: "/employers",
    label: "Employers",
    icon: Building2Icon,
    context: "The job boards we watch for you",
  },
  {
    to: "/criteria",
    label: "Criteria",
    icon: SlidersHorizontalIcon,
    context: "What you're looking for",
  },
  {
    to: "/runs",
    label: "Runs",
    icon: HistoryIcon,
    context: "Discovery history and outcomes",
  },
  {
    to: "/settings",
    label: "Settings",
    icon: SettingsIcon,
    context: "Models, schedule and spend",
  },
]
