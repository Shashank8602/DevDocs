import { useState } from "react";
import { UploadChanges } from "../../api/files";

const TILES = [
  { key: "created", label: "Created", tone: "text-emerald-700 bg-emerald-50", ring: "ring-emerald-500" },
  { key: "modified", label: "Modified", tone: "text-amber-700 bg-amber-50", ring: "ring-amber-500" },
  { key: "deleted", label: "Deleted", tone: "text-red-700 bg-red-50", ring: "ring-red-500" },
  { key: "unchanged", label: "Unchanged", tone: "text-slate-600 bg-slate-100", ring: "ring-slate-400" },
] as const;

/** Show the filename prominently and its folder muted, without the shared top-level project folder. */
function PathRow({ path, root }: { path: string; root: string }) {
  const rel = root && path.startsWith(root) ? path.slice(root.length) : path;
  const i = rel.lastIndexOf("/");
  return (
    <li className="truncate py-0.5" title={path}>
      <span className="text-slate-800">{rel.slice(i + 1)}</span>
      {i > 0 && <span className="ml-2 text-xs text-slate-400">{rel.slice(0, i)}</span>}
    </li>
  );
}

export default function UploadReportView({ report, skipped }: { report: UploadChanges; skipped: { count: number; reasons: string[] } }) {
  // Start on the first category that has files (created, modified, deleted); the user can click any tile.
  const [selected, setSelected] = useState<(typeof TILES)[number]["key"] | null>(
    TILES.slice(0, 3).find((t) => report[t.key].length > 0)?.key ?? null,
  );
  const all = [...report.created, ...report.modified, ...report.deleted, ...report.unchanged];
  const first = all[0]?.split("/")[0];
  const root = first && all.every((p) => p.startsWith(first + "/")) ? first + "/" : "";
  const tile = TILES.find((t) => t.key === selected);

  return (
    <div>
      <div className="grid grid-cols-4 gap-2">
        {TILES.map((t) => {
          const count = report[t.key].length;
          return (
            <button
              key={t.key}
              type="button"
              disabled={count === 0}
              aria-pressed={selected === t.key}
              onClick={() => setSelected(selected === t.key ? null : t.key)}
              className={`rounded-md px-2 py-2 text-center transition ${t.tone} ${
                selected === t.key ? `ring-2 ${t.ring}` : "hover:brightness-95"
              } disabled:cursor-default disabled:opacity-50 disabled:hover:brightness-100`}
            >
              <p className="text-xl font-semibold leading-none">{count}</p>
              <p className="mt-1 text-xs">{t.label}</p>
            </button>
          );
        })}
      </div>

      {tile ? (
        <ul className="mt-3 max-h-48 overflow-auto rounded-md border border-slate-100 px-3 py-2 text-sm">
          {report[tile.key].map((p) => <PathRow key={p} path={p} root={root} />)}
        </ul>
      ) : (
        <p className="mt-3 text-xs text-slate-400">Select a count to see its files.</p>
      )}

      {skipped.count > 0 && (
        <p className="mt-3 text-xs text-slate-400">
          {skipped.count.toLocaleString()} other file{skipped.count === 1 ? "" : "s"} skipped ({skipped.reasons.join(", ")}).
        </p>
      )}
    </div>
  );
}
