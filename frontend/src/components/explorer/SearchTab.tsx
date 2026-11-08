import { useQuery } from "@tanstack/react-query";
import { FormEvent, ReactNode, useEffect, useState } from "react";
import { insightsApi, SearchHit } from "../../api/insights";
import { Badge, EmptyState, FileIcon, Icon, Skeleton, Spinner } from "../ui";

function escapeRegExp(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Wrap each search word found in the snippet. Plain React nodes: nothing is inserted as HTML. */
function Highlight({ text, query }: { text: string; query: string }): ReactNode {
  const words = query.split(/\s+/).filter((w) => w.length > 1).map(escapeRegExp);
  if (words.length === 0) return text;
  const parts = text.split(new RegExp(`(${words.join("|")})`, "gi"));
  return parts.map((p, i) => (i % 2 === 1 ? <mark key={i}>{p}</mark> : p));
}

function lines(hit: SearchHit) {
  if (hit.line_start == null) return hit.page_number ? `page ${hit.page_number}` : "";
  return hit.line_end && hit.line_end !== hit.line_start ? `lines ${hit.line_start}-${hit.line_end}` : `line ${hit.line_start}`;
}

type Mode = "text" | "symbols";

/** Keyword search over the files the person can read. No embedding call, so it is instant and free. */
export default function SearchTab({
  projectId, onAsk, onOpenFile,
}: { projectId: string; onAsk: (q: string) => void; onOpenFile: (fileId: string, line?: number) => void }) {
  const [mode, setMode] = useState<Mode>("text");
  const [draft, setDraft] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [language, setLanguage] = useState("");
  const [fileType, setFileType] = useState("");
  const [folder, setFolder] = useState("");

  const dash = useQuery({ queryKey: ["projects", projectId, "dashboard"], queryFn: () => insightsApi.dashboard(projectId) });
  const text = useQuery({
    queryKey: ["projects", projectId, "search", submitted, language, fileType, folder],
    queryFn: () => insightsApi.search(projectId, submitted, { language, file_type: fileType, folder }),
    placeholderData: (previous) => previous, // keep the last results on screen while the next ones load
    enabled: mode === "text" && submitted.trim().length >= 2,
  });
  // Both searches follow the text box: results appear shortly after you stop typing, no button needed.
  const [symbolQuery, setSymbolQuery] = useState("");
  useEffect(() => {
    const t = setTimeout(() => {
      setSymbolQuery(draft);
      setSubmitted(draft.trim().length >= 2 ? draft.trim() : "");
    }, 250);
    return () => clearTimeout(t);
  }, [draft]);
  const symbols = useQuery({
    queryKey: ["projects", projectId, "symbols", symbolQuery],
    queryFn: () => insightsApi.symbols(projectId, symbolQuery),
    enabled: mode === "symbols",
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(draft.trim());
  };

  return (
    <div>
      <div role="tablist" className="segmented mb-4">
        {(["text", "symbols"] as const).map((m) => (
          <button key={m} role="tab" aria-selected={mode === m} onClick={() => setMode(m)} className="flex items-center gap-1.5">
            <Icon name={m === "text" ? "file-text" : "code"} className="h-4 w-4" />
            {m === "text" ? "Text search" : "Symbols"}
          </button>
        ))}
      </div>

      <form onSubmit={submit} className="flex flex-wrap items-end gap-3" role="search">
        <label className="min-w-[16rem] flex-1">
          <span className="label">{mode === "text" ? "Search the content, paths and function names" : "Find a function, class or type"}</span>
          <span className="relative block">
            <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              value={draft} onChange={(e) => setDraft(e.target.value)} className="input pl-9" autoFocus
              placeholder={mode === "text" ? "for example: retry on timeout" : "for example: parse"}
            />
          </span>
        </label>
        {mode === "text" && (
          <>
            <label>
              <span className="label">Language</span>
              <select value={language} onChange={(e) => setLanguage(e.target.value)} className="input select">
                <option value="">Any</option>
                {dash.data?.languages.filter((l) => l.language).map((l) => <option key={l.language} value={l.language!}>{l.language}</option>)}
              </select>
            </label>
            <label>
              <span className="label">Type</span>
              <select value={fileType} onChange={(e) => setFileType(e.target.value)} className="input select">
                <option value="">Any</option><option value="source">Source</option><option value="doc">Docs</option><option value="other">Other</option>
              </select>
            </label>
            <label>
              <span className="label">Folder</span>
              <input value={folder} onChange={(e) => setFolder(e.target.value)} className="input w-40" placeholder="src/api" />
            </label>
            <span className="flex items-center gap-1.5 pb-2.5 text-xs text-slate-500" aria-live="polite">
              {text.isFetching && <Spinner className="h-3 w-3" />}
              {text.isFetching ? "Searching…" : submitted ? `${text.data?.hits.length ?? 0} results` : ""}
            </span>
          </>
        )}
      </form>
      <p className="hint mb-5 flex items-center gap-1.5"><Icon name="check" className="h-3.5 w-3.5 text-emerald-700" /> No AI quota is used: this searches the words in your files.</p>

      {mode === "text" && (
        <>
          {text.isError && <p role="alert" className="text-sm text-red-600">{(text.error as Error).message}</p>}
          {!submitted && <EmptyState icon="search" title="Search your project">Type a word, a function name or a phrase. Results come from the files you can read.</EmptyState>}
          {text.data && text.data.hits.length === 0 && <EmptyState icon="search" title="No matches">Nothing in {text.data.searched_files} files matches "{submitted}". Try fewer or different words.</EmptyState>}
          <ul className="space-y-3">
            {text.data?.hits.map((h) => (
              <li key={`${h.file_id}-${h.line_start}-${h.score}`} className="card animate-fade-in overflow-hidden transition hover:border-slate-300 hover:shadow-lift">
                <div className="flex flex-wrap items-center justify-between gap-2 px-4 pb-2 pt-3">
                  <button className="flex min-w-0 items-center gap-2 text-left font-mono text-sm text-brand-link hover:underline" onClick={() => onOpenFile(h.file_id, h.line_start ?? undefined)}>
                    <FileIcon name={h.path} />
                    <span className="break-all">{h.path}</span>
                  </button>
                  <span className="flex flex-wrap gap-1.5 text-xs text-slate-500">
                    {h.section && <span className="rounded-md bg-slate-100 px-1.5 py-0.5 font-mono">{h.section}</span>}
                    {lines(h) && <span className="rounded-md bg-slate-100 px-1.5 py-0.5">{lines(h)}</span>}
                  </span>
                </div>
                <pre className="selectable-text mx-4 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-200 bg-slate-50 p-3 font-mono text-xs leading-5 text-slate-700">
                  <Highlight text={h.snippet} query={submitted} />
                </pre>
                <div className="flex justify-end px-2 py-1.5">
                  <button className="btn-ghost px-2 py-1 text-xs text-brand-link" onClick={() => onAsk(`Explain ${h.section ? `\`${h.section}\` in ` : ""}${h.path}${h.line_start ? ` around line ${h.line_start}` : ""}`)}>
                    <Icon name="chat" className="h-3.5 w-3.5" /> Ask about this
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {mode === "symbols" && (
        <>
          {symbols.isLoading && <Skeleton className="h-32" />}
          {symbols.data && symbols.data.length === 0 && <EmptyState icon="search" title="No symbols found">Nothing matches. Functions and classes are read from Python, JavaScript, TypeScript, Java, Go, Rust, C#, Ruby, PHP and C files.</EmptyState>}
          <ul className="card divide-y divide-slate-100">
            {symbols.data?.map((s) => (
              <li key={`${s.file_id}-${s.line}-${s.name}`} className="flex items-center gap-3 px-3.5 py-2 transition-colors hover:bg-slate-100/60">
                <span className="w-20 shrink-0"><Badge tone={s.kind === "class" ? "warning" : s.kind === "function" || s.kind === "method" ? "brand" : "neutral"}>{s.kind}</Badge></span>
                <button className="min-w-0 flex-1 truncate text-left font-mono text-sm text-slate-800 hover:text-brand-link" onClick={() => onOpenFile(s.file_id, s.line)} title={`${s.path}:${s.line}`}>
                  {s.name}
                </button>
                <span className="hidden truncate font-mono text-xs text-slate-500 sm:block">{s.path}:{s.line}</span>
                <button className="btn-ghost px-2 py-0.5 text-xs text-brand-link" onClick={() => onAsk(`What does \`${s.name}\` in ${s.path} do?`)}><Icon name="chat" className="h-3.5 w-3.5" /> Ask</button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
