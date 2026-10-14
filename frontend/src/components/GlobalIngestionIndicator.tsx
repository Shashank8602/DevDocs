import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { filesApi } from "../api/files";

/** Shown in the header on every page while any project is being ingested; hovering names the project(s). */
export default function GlobalIngestionIndicator() {
  const qc = useQueryClient();
  const { data: active = [] } = useQuery({
    queryKey: ["ingestion", "active"],
    queryFn: filesApi.activeIngestion,
    refetchInterval: 2000,
  });

  // When a project drops off the list its status and files changed, so refresh whatever is on screen.
  const previous = useRef<string[]>([]);
  useEffect(() => {
    const ids = active.map((a) => a.project_id);
    if (previous.current.some((id) => !ids.includes(id))) qc.invalidateQueries({ queryKey: ["projects"] });
    previous.current = ids;
  }, [active, qc]);

  if (active.length === 0) return null;
  const done = active.reduce((n, a) => n + (a.total - a.remaining), 0);
  const total = active.reduce((n, a) => n + a.total, 0);
  const pct = total ? Math.round((done / total) * 100) : 0;
  const label = `Processing ${done}/${total}`;
  return (
    <div className="group relative">
      <Link
        to={`/projects/${active[0].project_id}`}
        aria-label={`${label}: ${active.map((a) => a.project_name).join(", ")}`}
        className="flex items-center gap-2 rounded-lg border border-slate-300 bg-surface px-3 py-1.5 text-sm text-slate-700 shadow-sm"
      >
        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" aria-hidden="true" />
        <span className="tabular-nums">{label}</span>
      </Link>
      <div
        role="tooltip"
        className="pointer-events-none absolute right-0 top-full z-50 mt-2 w-72 translate-y-1 rounded-xl border border-slate-200 bg-surface p-3 opacity-0 shadow-lift transition group-hover:translate-y-0 group-hover:opacity-100"
      >
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Being processed</p>
        <ul className="mt-2 space-y-3">
          {active.map((a) => {
            const d = a.total - a.remaining;
            return (
              <li key={a.project_id}>
                <div className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="truncate font-medium text-slate-800">{a.project_name}</span>
                  <span className="shrink-0 tabular-nums text-xs text-slate-500">{d} of {a.total} files</span>
                </div>
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-200">
                  <div className="h-full rounded-full bg-brand-600 transition-all duration-500" style={{ width: `${a.total ? (d / a.total) * 100 : 0}%` }} />
                </div>
              </li>
            );
          })}
        </ul>
        <p className="mt-2 text-right text-xs text-slate-500">{pct}% overall</p>
      </div>
    </div>
  );
}
