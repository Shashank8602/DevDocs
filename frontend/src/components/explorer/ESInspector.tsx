import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { filesApi } from "../../api/files";
import Modal from "../Modal";
import VectorPeek from "./VectorPeek";

const STATUS_LABEL: Record<string, string> = { pending: "Pending", processing: "Processing", ready: "Ready", failed: "Failed" };

/** Dev-only (DEV_MODE=true): does Elasticsearch agree with Postgres for this project? Manual refresh only. */
export default function ESInspector({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ["projects", projectId, "es-inspector"],
    queryFn: () => filesApi.esInspector(projectId),
    staleTime: Infinity,
    gcTime: 0,
  });
  const [peeking, setPeeking] = useState<string | null>(null);
  const inSync =data ? data.totals.postgres === data.totals.es && data.orphans.length === 0 : false;

  return (
    <Modal title="ES Inspector" onClose={onClose} wide>
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="min-w-0 truncate text-xs text-slate-500">ES index: {data?.index ?? "…"}</p>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="rounded-md border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100 disabled:opacity-50"
        >
          {isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {isLoading && <p className="text-sm text-slate-500">Loading…</p>}
      {isError && <p className="text-sm text-red-600">Couldn't load the inspector. Is DEV_MODE=true on the backend?</p>}
      {data && (
        <>
          <p className="mb-3 text-sm text-slate-700">
            Total chunks — Postgres: <b>{data.totals.postgres}</b> · Elasticsearch: <b>{data.totals.es}</b>{" "}
            <span className={inSync ? "text-green-700" : "text-red-600"}>{inSync ? "✔ in sync" : "✖ out of sync"}</span>
          </p>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
                <th className="py-1 pr-3 font-medium">File</th>
                <th className="py-1 pr-3 font-medium">Status</th>
                <th className="py-1 pr-3 text-right font-medium">Postgres chunks</th>
                <th className="py-1 pr-3 text-right font-medium">ES chunks</th>
                <th className="py-1 pr-3 font-medium">Match</th>
                <th className="py-1 font-medium">Vectors</th>
              </tr>
            </thead>
            <tbody>
              {data.files.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-3 text-slate-500">This project has no files.</td>
                </tr>
              )}
              {data.files.map((f) => {
                const ready = f.status === "ready";
                const ok = f.postgres_chunks === f.es_chunks;
                return (
                  <Fragment key={f.file_id}>
                    <tr className="border-b border-slate-100">
                      <td className="max-w-[18rem] truncate py-1 pr-3" title={f.path}>{f.path}</td>
                      <td className="py-1 pr-3">{STATUS_LABEL[f.status] ?? f.status}</td>
                      <td className="py-1 pr-3 text-right tabular-nums">{f.postgres_chunks}</td>
                      <td className="py-1 pr-3 text-right tabular-nums">{f.es_chunks}</td>
                      <td className="py-1 pr-3">
                        {ready ? (
                          ok ? <span className="text-green-700">✔</span> : <span className="font-medium text-red-600">✖</span>
                        ) : (
                          <span className="text-slate-400">– {f.status === "failed" ? "" : "(not indexed yet)"}</span>
                        )}
                      </td>
                      <td className="py-1">
                        {f.es_chunks > 0 ? (
                          <button
                            onClick={() => setPeeking(peeking === f.file_id ? null : f.file_id)}
                            aria-expanded={peeking === f.file_id}
                            className="rounded border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-100"
                          >
                            {peeking === f.file_id ? "Hide" : "Peek"}
                          </button>
                        ) : (
                          <span className="text-slate-300">–</span>
                        )}
                      </td>
                    </tr>
                    {peeking === f.file_id && (
                      <tr>
                        <td colSpan={6} className="p-0">
                          <VectorPeek projectId={projectId} fileId={f.file_id} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          <div className="mt-4 text-sm">
            {data.orphans.length === 0 ? (
              <p className="text-slate-600">Orphans in ES (docs whose file no longer exists in Postgres): none</p>
            ) : (
              <div className="rounded-md border border-red-200 bg-red-50 p-3 text-red-700">
                <p className="font-medium">Orphans in ES: {data.orphans.reduce((n, o) => n + o.es_chunks, 0)} doc(s)</p>
                <ul className="mt-1 text-xs">
                  {data.orphans.map((o) => (
                    <li key={o.file_id}>{o.file_id}: {o.es_chunks}</li>
                  ))}
                </ul>
                <p className="mt-2 text-xs">
                  Run <code>python -m app.search.reconcile</code> from <code>backend/</code> to remove them.
                </p>
              </div>
            )}
          </div>
        </>
      )}
    </Modal>
  );
}
