import { useMutation } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { filesApi, PipelineHit, PlaygroundHit } from "../../api/files";
import Modal from "../Modal";

const DROPPED_LABEL: Record<string, string> = {
  gate: "question refused by the gate",
  relative: "far below the best chunk",
  threshold: "below threshold",
  overlap: "overlaps a better chunk",
  top_k: "over Top-K",
  budget: "over token budget",
};

function Location({ h }: { h: { file_path: string; line_start: number | null; line_end: number | null; section: string | null; page_number?: number | null } }) {
  return (
    <>
      <span className="min-w-0 truncate font-medium text-slate-800" title={h.file_path}>{h.file_path}</span>
      {h.line_start != null && <span>lines {h.line_start}–{h.line_end}</span>}
      {h.page_number != null && <span>page {h.page_number}</span>}
      {h.section && <span className="min-w-0 max-w-full truncate" title={h.section}>{h.section}</span>}
    </>
  );
}

function LegColumn({ title, hits, empty }: { title: string; hits: PlaygroundHit[]; empty: string }) {
  return (
    <section className="min-w-0 flex-1">
      <h3 className="mb-2 text-sm font-semibold text-slate-700">{title}</h3>
      {hits.length === 0 ? (
        <p className="text-sm text-slate-500">{empty}</p>
      ) : (
        <ol className="space-y-1">
          {hits.map((h) => (
            <li key={h.chunk_id} className="flex flex-wrap gap-x-3 rounded border border-slate-200 px-2 py-1 text-xs text-slate-600">
              <span className="font-medium">#{h.rank}</span>
              <Location h={h} />
              <span className="ml-auto tabular-nums">score {h.score.toFixed(3)}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function CandidateRow({ h }: { h: PipelineHit }) {
  return (
    <li className={`min-w-0 overflow-hidden rounded-md border ${h.kept ? "border-green-300" : "border-slate-200"}`}>
      <div className={`flex flex-wrap gap-x-3 gap-y-0.5 px-2 py-1 text-xs ${h.kept ? "bg-green-50 text-green-900" : "bg-slate-50 text-slate-600"}`}>
        <span className="font-medium">#{h.rank}</span>
        <Location h={h} />
        <span className="tabular-nums">BM25 {h.bm25_rank ?? "–"} · vec {h.vector_rank ?? "–"} · RRF {h.rrf_score.toFixed(4)}</span>
        <span className="ml-auto font-medium tabular-nums">
          similarity {h.vector_score == null ? "–" : h.vector_score.toFixed(3)}
          {h.rerank_score != null && ` · rerank ${h.rerank_score.toFixed(2)}`}
        </span>
        <span className="font-medium">
          {h.kept ? `kept · ${h.token_count} tok` : `dropped: ${DROPPED_LABEL[h.dropped_by ?? ""] ?? h.dropped_by}`}
        </span>
      </div>
      <pre className="max-h-24 overflow-auto whitespace-pre-wrap break-words p-2 text-xs">{h.content}</pre>
    </li>
  );
}

/** Dev-only (DEV_MODE=true): the whole retrieval pipeline for a query, every stage visible. */
export default function RetrievalPlayground({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [gate, setGate] = useState("");
  const [cutoff, setCutoff] = useState("");
  const search = useMutation({
    mutationFn: (v: { query: string; gate?: number; cutoff?: number }) =>
      filesApi.pipeline(projectId, v.query, v.gate, v.cutoff),
  });
  const num = (s: string) => (s.trim() === "" || !Number.isFinite(Number(s)) ? undefined : Number(s));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (q.trim()) search.mutate({ query: q.trim(), gate: num(gate), cutoff: num(cutoff) });
  };
  const r = search.data;

  return (
    <Modal title="Retrieval Playground" onClose={onClose} wide>
      <form onSubmit={submit} className="flex flex-wrap gap-2">
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="e.g. validate_token, or: where do we verify the user's identity"
          aria-label="Query"
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        <input
          value={gate}
          onChange={(e) => setGate(e.target.value)}
          placeholder={r ? `gate (${r.gate_threshold})` : "gate (default)"}
          aria-label="Gate threshold override"
          inputMode="decimal"
          className="w-32 rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        <input
          value={cutoff}
          onChange={(e) => setCutoff(e.target.value)}
          placeholder={r ? `cut-off (${r.relative_cutoff})` : "cut-off (default)"}
          aria-label="Relative cut-off override"
          inputMode="decimal"
          className="w-32 rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={search.isPending || !q.trim()}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50"
        >
          {search.isPending ? "Searching…" : "Search"}
        </button>
      </form>
      {search.isError && <p className="mt-3 text-sm text-red-600">Search failed. Is DEV_MODE=true on the backend?</p>}
      {r && (
        <>
          <p className="mt-3 text-xs text-slate-500">
            {r.ready_files} Ready file{r.ready_files === 1 ? "" : "s"} searched · index {r.index} · {r.fused} candidate
            {r.fused === 1 ? "" : "s"} after RRF · reranker {r.reranked ? "on" : "off"}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Gate: top vector similarity {r.top_vector_score == null ? "–" : r.top_vector_score.toFixed(3)} vs{" "}
            {r.gate_threshold} — {r.gate_passed ? "passed" : "refused"} · cut-off {r.relative_cutoff} below the best chunk
            (the best {r.bm25_protect} BM25 hits are kept)
          </p>
          {r.error && <p className="mt-2 text-sm text-red-600">{r.error}</p>}

          <div
            className={`mt-3 rounded-md border px-3 py-2 text-sm font-medium ${
              r.no_relevant_context ? "border-amber-300 bg-amber-50 text-amber-900" : "border-green-300 bg-green-50 text-green-900"
            }`}
          >
            {r.no_relevant_context
              ? "No relevant context — the answer LLM would not be called."
              : `Final context: ${r.final.length} chunk${r.final.length === 1 ? "" : "s"} · ${r.context_tokens} / ${r.token_budget} tokens · Top-K ${r.top_k}`}
          </div>

          <h3 className="mb-2 mt-4 text-sm font-semibold text-slate-700">Candidates ({r.candidates.length}) — in serving order</h3>
          {r.candidates.length === 0 ? (
            <p className="text-sm text-slate-500">Nothing matched the query.</p>
          ) : (
            <ol className="space-y-2">{r.candidates.map((h) => <CandidateRow key={h.chunk_id} h={h} />)}</ol>
          )}

          <details className="mt-4">
            <summary className="cursor-pointer text-sm font-semibold text-slate-700">BM25 and vector Top-10 (before fusion)</summary>
            <div className="mt-2 flex flex-col gap-4 md:flex-row">
              <LegColumn title="BM25" hits={r.bm25} empty="No keyword matches." />
              <LegColumn title="Vector" hits={r.vector} empty="No vector results." />
            </div>
          </details>
        </>
      )}
    </Modal>
  );
}
