import {
  Navigate,
  RouterProvider,
  createBrowserRouter,
  type RouteObject,
} from "react-router-dom"
import { ThemeProvider } from "@/components/theme/ThemeProvider"
import { AppShell } from "@/components/shell/AppShell"
import Criteria from "@/pages/Criteria"
import Dashboard from "@/pages/Dashboard"
import Employers from "@/pages/Employers"
import Listings from "@/pages/Listings"
import ProposalsPage from "@/pages/proposals/ProposalsPage"
import Runs from "@/pages/Runs"
import Settings from "@/pages/Settings"

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: "listings", element: <Listings /> },
      { path: "employers", element: <Employers /> },
      { path: "criteria", element: <Criteria /> },
      { path: "proposals", element: <ProposalsPage /> },
      { path: "runs", element: <Runs /> },
      { path: "settings", element: <Settings /> },
      // GAP-3: Get Started was merged into Criteria. Old links land here.
      { path: "onboarding", element: <Navigate to="/criteria" replace /> },
    ],
  },
]

export const router = createBrowserRouter(routes)

export default function App() {
  return (
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>
  )
}
