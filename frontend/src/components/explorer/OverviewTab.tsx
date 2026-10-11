import { useQuery } from "@tanstack/react-query";
import { filesApi } from "../../api/files";

const TYPE_LABEL = { source: "Source code", doc: "Documentation", other: "Other" } as const;
const PALETTE = ["bg-brand-600", "bg-emerald-500", "bg-amber-500", "bg-sky-500", "bg-rose-500", "bg-violet-500"];

export default function OverviewTab({ projectId }: { projectId: string }) {
  const { data, isLoading, isError } = useQuery({ queryKey: ["projects", projectId, "overview"], queryFn: () => filesApi.overview(projectId) });
  if (isError) {
    return (
      <p role="alert" className="text-sm text-red-600">
        Couldn't load the project overview. Restart the backend (it may be an older version) and reload this page.
      </p>
    );
  }
  if (isLoading || !data) return <p className="text-slate-500">Loading…</p>;

  // Top 5 languages; the rest are grouped so the bar stays readable.
  const top = data.languages.slice(0, 5).map((l) => ({ name: l.language ?? "", count: l.count }));
  const rest = data.languages.slice(5).reduce((n, l) => n + l.count, 0);
  const segments = rest ? [...top, { name: "other languages", count: rest }] : top;
  const total = segments.reduce((n, s) => n + s.count, 0);

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-4">
        <Stat label="Files" value={data.file_count} />
        {(Object.keys(TYPE_LABEL) as (keyof typeof TYPE_LABEL)[]).map((k) => (
          <Stat key={k} label={TYPE_LABEL[k]} value={data.by_type[k]} />
        ))}
      </div>

      <div className="rounded-lg border border-slate-200 bg-surface p-4">
        <h2 className="text-sm font-medium text-slate-700">Languages</h2>
        {total === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No source languages detected.</p>
        ) : (
          <>
            <div className="mt-3 flex h-3 overflow-hidden rounded-full bg-slate-100" role="img" aria-label="Language breakdown">
              {segments.map((s, i) => (
                <div key={s.name} className={PALETTE[i % PALETTE.length]} style={{ width: `${(s.count / total) * 100}%` }} title={`${s.name}: ${s.count}`} />
              ))}
            </div>
            <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm text-slate-600">
              {segments.map((s, i) => (
                <li key={s.name} className="flex items-center gap-1.5">
                  <span className={`h-2.5 w-2.5 rounded-full ${PALETTE[i % PALETTE.length]}`} />
                  {s.name} <span className="text-slate-400">{Math.round((s.count / total) * 100)}%</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <p className="text-sm text-slate-500">
        Last updated: {data.last_updated ? new Date(data.last_updated).toLocaleString() : "—"}
      </p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-surface p-4">
      <p className="text-2xl font-semibold text-slate-900">{value}</p>
      <p className="text-sm text-slate-500">{label}</p>
    </div>
  );
}
