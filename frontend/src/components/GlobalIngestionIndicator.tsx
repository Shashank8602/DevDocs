import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { filesApi } from "../api/files";

/** Shown in the header on every page while any project is being ingested. */
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
  const tooltip = active
    .map((a) => `${a.project_name}: ${a.remaining} of ${a.total} file${a.total === 1 ? "" : "s"} left`)
    .join("\n");
  return (
    <Link
      to={`/projects/${active[0].project_id}`}
      title={tooltip}
      aria-label={`Processing: ${tooltip}`}
      className="flex items-center gap-2 rounded-md border border-slate-300 bg-surface px-3 py-1.5 text-sm text-slate-700"
    >
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" aria-hidden="true" />
      Processing…
    </Link>
  );
}
