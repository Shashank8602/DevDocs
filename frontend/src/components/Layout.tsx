import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, useLocation, useMatch, useNavigate } from "react-router-dom";
import { projectsApi } from "../api/projects";
import { useAuth } from "../auth/AuthContext";
import ServerStatus from "./ServerStatus";
import GlobalIngestionIndicator from "./GlobalIngestionIndicator";
import ThemeToggle from "./ThemeToggle";
import { Icon, Menu, Monogram } from "./ui";

function PreviewBanner() {
  const { user, stopPreview } = useAuth();
  return (
    <div role="status" className="flex shrink-0 flex-wrap items-center justify-center gap-3 border-b border-amber-500/30 bg-amber-50 px-4 py-2 text-sm text-amber-700">
      <Icon name="eye" />
      <span>
        Previewing the app as <strong>{user?.name || user?.email}</strong>. This is read-only and shows only what they can read.
      </span>
      <button onClick={stopPreview} className="btn-secondary px-3 py-1">Back to admin</button>
    </div>
  );
}

function UserMenu() {
  const { user, isAdmin, previewing, signOut } = useAuth();
  const navigate = useNavigate();
  const onSettings = useLocation().pathname === "/settings";
  const who = user?.name || user?.email || "";
  return (
    <Menu
      label="Account"
      className="flex h-8 items-center gap-1.5 rounded-full p-0.5 pr-1.5 transition hover:bg-slate-100"
      button={<><Monogram name={who} className="h-7 w-7 rounded-full text-[0.7rem]" /><Icon name="chevron-down" className="h-3.5 w-3.5 text-slate-500" /></>}
    >
      {(close) => (
        <>
          <div className="flex items-center gap-3 px-2.5 pb-2.5 pt-2">
            <Monogram name={who} className="h-9 w-9 rounded-full text-xs" />
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-slate-900">{user?.name || user?.email}</p>
              {user?.name && <p className="truncate text-xs text-slate-500">{user.email}</p>}
              <p className="mt-0.5 text-xs text-slate-500">{user?.role === "admin" ? "Admin" : "Member"}{previewing ? " (preview)" : ""}</p>
            </div>
          </div>
          <div className="my-1 border-t border-slate-200" />
          {isAdmin && (
            <button
              role="menuitem" aria-current={onSettings ? "page" : undefined}
              className={`menu-item ${onSettings ? "bg-brand-500/10 text-brand-link hover:bg-brand-500/10 hover:text-brand-link" : ""}`}
              onClick={() => { close(); navigate("/settings"); }}
            >
              <Icon name="settings" className={`h-4 w-4 ${onSettings ? "" : "text-slate-500"}`} /> Settings
            </button>
          )}
          {!previewing && (
            <button role="menuitem" className="menu-item" onClick={() => { close(); void signOut(); }}>
              <Icon name="logout" className="h-4 w-4 text-slate-500" /> Sign out
            </button>
          )}
        </>
      )}
    </Menu>
  );
}

export default function Layout() {
  const { previewing } = useAuth();
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
      {previewing && <PreviewBanner />}
      <header className="z-30 shrink-0 border-b border-slate-200/80 bg-surface/80 backdrop-blur-md">
        <div className={`flex h-14 items-center justify-between gap-4 px-4 ${workspace ? "" : "mx-auto max-w-6xl sm:px-6"}`}>
          <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-2 text-sm">
            <Link to="/" className="group flex shrink-0 items-center gap-2.5 font-semibold tracking-tight text-slate-900">
              <img src="/logo.svg" alt="" className="h-7 w-7 transition-transform duration-300 group-hover:rotate-6" />
              <span className={project ? "hidden sm:inline" : ""}>DevDocs</span>
            </Link>
            {project && (
              <>
                <Icon name="chevron-right" className="h-4 w-4 shrink-0 text-slate-300" />
                <Link to="/" className="hidden shrink-0 text-slate-500 transition hover:text-slate-900 md:inline">Projects</Link>
                <Icon name="chevron-right" className="hidden h-4 w-4 shrink-0 text-slate-300 md:block" />
                <span className="truncate font-medium text-slate-900" aria-current="page" title={project.name}>{project.name}</span>
              </>
            )}
          </nav>
          <div className="flex shrink-0 items-center gap-1.5">
            <GlobalIngestionIndicator />
            <ServerStatus />
            <span className="mx-1 h-5 w-px bg-slate-200" aria-hidden="true" />
            <ThemeToggle />
            <UserMenu />
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
