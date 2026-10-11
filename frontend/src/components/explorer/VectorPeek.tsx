import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { filesApi, VectorPeekChunk } from "../../api/files";

/** Average `values` into at most `n` buckets so 1536 numbers fit in a small bar chart. */
function downsample(values: number[], n: number): number[] {
  if (values.length <= n) return values;
  const size = values.length / n;
  return Array.from({ length: n }, (_, i) => {
    const slice = values.slice(Math.floor(i * size), Math.floor((i + 1) * size));
    return slice.reduce((a, b) => a + b, 0) / slice.length;
  });
}

function Bars({ values }: { values: number[] }) {
  const max = Math.max(...values.map(Math.abs), 1e-9);
  const w = 4;
  const h = 40;
  return (
    <svg role="img" aria-label="Vector values" width={values.length * w} height={h} className="max-w-none">
      <line x1={0} x2={values.length * w} y1={h / 2} y2={h / 2} stroke="currentColor" strokeOpacity={0.25} />
      {values.map((v, i) => {
        const bar = (Math.abs(v) / max) * (h / 2);
        return (
          <rect
            key={i}
            x={i * w}
            y={v >= 0 ? h / 2 - bar : h / 2}
            width={w - 1}
            height={Math.max(bar, 0.5)}
            className={v >= 0 ? "fill-brand-600" : "fill-red-500"}
          />
        );
      })}
    </svg>
  );
}

function ChunkVector({ c, full }: { c: VectorPeekChunk; full: boolean }) {
  const bars = c.values ? downsample(c.values, 96) : c.preview;
  return (
    <li className="min-w-0 overflow-hidden rounded-md border border-slate-200 bg-surface">
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 border-b border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600">
        <span className="font-medium">#{c.ordinal}</span>
        {c.line_start != null && <span>lines {c.line_start}–{c.line_end}</span>}
        {c.page_number != null && <span>page {c.page_number}</span>}
        <span>{c.token_count} tokens</span>
        <span className="ml-auto tabular-nums">
          {c.dims} dims · norm {c.norm.toFixed(3)} · min {c.min.toFixed(3)} · max {c.max.toFixed(3)}
        </span>
      </div>
      <p className="truncate px-2 pt-2 text-xs text-slate-500" title={c.content_preview}>{c.content_preview}</p>
      <div className="px-2 py-2">
        <div className="overflow-x-auto text-slate-400">
          <Bars values={bars} />
        </div>
        <p className="mt-1 text-xs text-slate-400">
          {c.values ? `all ${c.dims} values, averaged into ${bars.length} bars` : `first ${c.preview.length} of ${c.dims} values`}
          {" "}(<span className="text-brand-600">blue</span> positive, <span className="text-red-500">red</span> negative)
        </p>
        <p className="mt-1 break-all font-mono text-xs text-slate-700">
          [{c.preview.map((v) => v.toFixed(4)).join(", ")}, …]
        </p>
        {full && c.values && (
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-50 p-2 font-mono text-[11px] text-slate-700">
            {c.values.map((v) => v.toFixed(6)).join(", ")}
          </pre>
        )}
      </div>
    </li>
  );
}

/** Dev-only: the embedding vectors Elasticsearch stores for one file, chunk by chunk. */
export default function VectorPeek({ projectId, fileId }: { projectId: string; fileId: string }) {
  const [full, setFull] = useState(false);
  const { data, isLoading, isError } = useQuery({
    queryKey: ["projects", projectId, "files", fileId, "vectors", full],
    queryFn: () => filesApi.vectors(projectId, fileId, full),
    staleTime: Infinity,
  });

  return (
    <div className="bg-slate-50/60 p-3">
      {isLoading && <p className="text-sm text-slate-500">Loading vectors…</p>}
      {isError && <p className="text-sm text-red-600">Could not load the vectors.</p>}
      {data && (
        <>
          <div className="mb-2 flex items-center justify-between gap-3 text-xs text-slate-500">
            <span>
              {data.chunks.length} of {data.postgres_chunks} chunk{data.postgres_chunks === 1 ? "" : "s"} have a vector in ES
            </span>
            <button onClick={() => setFull(!full)} className="rounded border border-slate-300 px-2 py-0.5 hover:bg-white">
              {full ? "Hide all values" : "Show all values"}
            </button>
          </div>
          {data.chunks.length === 0 ? (
            <p className="text-sm text-slate-500">No vectors stored for this file (status: {data.file_status}).</p>
          ) : (
            <ol className="space-y-2">
              {data.chunks.map((c) => (
                <ChunkVector key={c.chunk_id} c={c} full={full} />
              ))}
            </ol>
          )}
        </>
      )}
    </div>
  );
}
