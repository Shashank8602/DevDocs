import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Outlet, useLocation, useMatch } from "react-router-dom";
import { projectsApi } from "../api/projects";
import ServerStatus from "./ServerStatus";
import GlobalIngestionIndicator from "./GlobalIngestionIndicator";
import ThemeToggle from "./ThemeToggle";
import { Icon } from "./ui";

export default function Layout() {
  // A project workspace fills the window and scrolls inside its own panes; other pages scroll as a page.
  const workspace = /^\/projects\/[^/]+/.test(useLocation().pathname);
  const projectId = useMatch("/projects/:projectId")?.params.projectId;
  const { data: project } = useQuery({
    queryKey: ["projects", projectId],
    queryFn: () => projectsApi.get(projectId as string),
    enabled: !!projectId,
  });
  return (
    <div className="flex h-screen flex-col">
      <header className="z-30 shrink-0 border-b border-slate-200/80 bg-surface/80 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
          <Link to="/" className="group flex items-center gap-2.5 font-semibold tracking-tight">
            <img src="/logo.svg" alt="" className="h-7 w-7 transition-transform duration-300 group-hover:rotate-6" />
            <span>{project ? project.name : "DevDocs"}</span>
          </Link>
          <div className="flex items-center gap-2">
            <GlobalIngestionIndicator />
            <ThemeToggle />
            <NavLink
              to="/settings"
              className={({ isActive }) => `btn-secondary ${isActive ? "border-brand-500 text-brand-link" : ""}`}
            >
              <Icon name="settings" />
              <span className="hidden sm:inline">Settings</span>
            </NavLink>
            <ServerStatus />
          </div>
        </div>
      </header>
      {workspace ? (
        <main className="min-h-0 flex-1 animate-fade-in">
          <Outlet />
        </main>
      ) : (
        <main className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-6xl animate-fade-in px-4 py-8 sm:px-6">
            <Outlet />
          </div>
        </main>
      )}
    </div>
  );
}
