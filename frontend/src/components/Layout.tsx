import { Link, Outlet } from "react-router-dom";
import GlobalIngestionIndicator from "./GlobalIngestionIndicator";
import ThemeToggle from "./ThemeToggle";

export default function Layout() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-surface">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
          <Link to="/" className="flex items-center gap-2 font-semibold">
            <img src="/logo.svg" alt="" className="h-7 w-7" />
            DevDocs
          </Link>
          <div className="flex items-center gap-2">
            <GlobalIngestionIndicator />
            <ThemeToggle />
            <Link
              to="/settings"
              className="rounded-md border border-slate-300 bg-surface px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-100 active:scale-95"
            >
              Settings
            </Link>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}
