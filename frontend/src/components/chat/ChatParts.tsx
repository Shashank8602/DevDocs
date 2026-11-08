import { useEffect, useRef, useState } from "react";
import { ChatMessage, ContextInspection, SourceRef } from "../../api/chat";
import Modal from "../Modal";
import { Badge, CopyButton, FileIcon, Icon, Spinner } from "../ui";
import AssistantMarkdown from "./AssistantMarkdown";

// Purely cosmetic: they say nothing about what the backend is doing.
const LOADING_PHRASES = [
  "Thinking…", "Connecting the dots…", "Digging in…", "Reading between the lines…",
  "Piecing it together…", "Tracing the code…", "Almost there…",
];

export function ThinkingIndicator({ waitSeconds }: { waitSeconds: number | null }) {
  const [i, setI] = useState(0);
  const [left, setLeft] = useState(waitSeconds);
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % LOADING_PHRASES.length), 1800);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    setLeft(waitSeconds);
    if (waitSeconds == null) return;
    const t = setInterval(() => setLeft((s) => (s == null ? s : Math.max(s - 1, 0))), 1000);
    return () => clearInterval(t);
  }, [waitSeconds]);
  return (
    <div className="flex items-center gap-3 text-sm text-slate-500" role="status">
      <span className="flex gap-1" aria-hidden="true">
        {[0, 1, 2].map((d) => (
          <span key={d} className="h-1.5 w-1.5 animate-dot rounded-full bg-brand-500" style={{ animationDelay: `${d * 0.16}s` }} />
        ))}
      </span>
      <span key={i} className="animate-fade-in">
        {waitSeconds != null ? `Waiting for the model's rate limit… about ${left}s` : LOADING_PHRASES[i]}
      </span>
    </div>
  );
}

export function AssistantAvatar() {
  return <img src="/logo.svg" alt="" aria-hidden="true" className="mt-0.5 h-7 w-7 shrink-0 rounded-lg shadow-sm" />;
}

const fileName = (p: string) => p.split("/").pop() ?? p;
const folderOf = (p: string) => p.split("/").slice(0, -1).join("/");

export function SourceChips({ sources, onOpen }: { sources: SourceRef[]; onOpen: (s: SourceRef) => void }) {
  if (!sources.length) return null;
  return (
    <div className="mt-4">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-slate-500">
        <Icon name="book" className="h-3.5 w-3.5" /> Sources <span className="tabular-nums text-slate-400">{sources.length}</span>
      </p>
      <ul className="grid gap-2 sm:grid-cols-2">
        {sources.map((s, i) => s.available === false || !s.file_id ? (
          <li key={`gone-${i}`}>
            <span
              className="flex h-full items-center gap-2.5 rounded-xl border border-dashed border-slate-300 px-3 py-2 text-xs text-slate-500"
              title="You no longer have access to this file"
            >
              <Icon name="lock" className="h-4 w-4 shrink-0" />
              Source no longer available
            </span>
          </li>
        ) : (
          <li key={s.file_id}>
            <button
              onClick={() => onOpen(s)}
              title={s.label}
              className="group flex h-full w-full items-center gap-2.5 rounded-xl border border-slate-200 bg-surface px-3 py-2 text-left shadow-sm transition hover:-translate-y-px hover:border-brand-500/50 hover:shadow-card"
            >
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-slate-100 text-xs font-semibold tabular-nums text-slate-600 group-hover:bg-brand-500/10 group-hover:text-brand-link">
                {i + 1}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <FileIcon name={s.file_path} className="h-3.5 w-3.5" />
                  <span className="truncate font-mono text-xs font-medium text-slate-800">{fileName(s.file_path)}</span>
                </span>
                <span className="mt-0.5 flex items-center gap-1.5 text-xs text-slate-500">
                  {folderOf(s.file_path) && <span className="min-w-0 truncate">{folderOf(s.file_path)}</span>}
                  {s.ranges.length > 0 && (
                    <span className="shrink-0 rounded bg-amber-500/15 px-1.5 font-medium tabular-nums text-amber-700">
                      L{s.ranges.map(([a, b]) => (a === b ? a : `${a}–${b}`)).join(", ")}
                    </span>
                  )}
                </span>
              </span>
              <Icon name="arrow-right" className="h-3.5 w-3.5 shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-brand-link" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function MessageView({ m, onOpenSource }: { m: ChatMessage; onOpenSource: (s: SourceRef) => void }) {
  if (m.role === "user") {
    return (
      <div className="group flex animate-slide-up flex-col items-end">
        <p className="selectable-text max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-brand-600 px-4 py-2.5 text-[15px] leading-6 text-white shadow-sm selection:bg-white/35 selection:text-white">
          {m.content}
        </p>
        <div className="mt-1 opacity-60 transition group-hover:opacity-100 focus-within:opacity-100">
          <CopyButton text={m.content} className="text-slate-500" />
        </div>
      </div>
    );
  }
  return (
    <div className="group flex animate-slide-up gap-3">
      <AssistantAvatar />
      <div className="min-w-0 flex-1">
        <AssistantMarkdown text={m.content} />
        {m.sources_json && <SourceChips sources={m.sources_json} onOpen={onOpenSource} />}
        <div className="mt-1 opacity-60 transition group-hover:opacity-100 focus-within:opacity-100">
          <CopyButton text={m.content} className="text-slate-500" />
        </div>
      </div>
    </div>
  );
}

export function StoppedBar({
  kind, onRetry, onCancel,
}: { kind: "stopped" | "failed"; onRetry: () => void; onCancel: () => void }) {
  return (
    <div
      role="alert"
      className="flex animate-slide-up flex-wrap items-center gap-3 rounded-xl border border-amber-500/30 bg-amber-50 px-4 py-3 text-sm"
    >
      <Icon name="alert" className="h-4 w-4 shrink-0 text-amber-700" />
      <span className="mr-auto font-medium text-amber-700">
        {kind === "stopped" ? "Stopped" : "Couldn't generate a response. Please try again."}
      </span>
      <button onClick={onRetry} className="btn-secondary px-3 py-1.5">
        <Icon name="refresh" className="h-3.5 w-3.5" /> Retry
      </button>
      <button onClick={onCancel} className="btn-ghost px-3 py-1.5">Cancel</button>
    </div>
  );
}

/** `starters` come from the project's own files (see GET /projects/{id}/starters); there are no fixed examples. */
export function BlankState({ onPick, starters }: { onPick: (q: string) => void; starters: string[] }) {
  return (
    <div className="mx-auto flex max-w-2xl animate-fade-in flex-col items-center px-4 py-14 text-center">
      <span className="relative isolate">
        <span aria-hidden="true" className="absolute inset-0 -z-10 scale-150 rounded-full bg-brand-500/20 blur-2xl" />
        <img src="/logo.svg" alt="" className="h-16 w-16 rounded-2xl shadow-lift ring-4 ring-brand-500/10" />
      </span>
      <h2 className="mt-6 text-2xl font-semibold tracking-tight text-slate-900">Ask anything about your project</h2>
      <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">
        Answers are grounded in your files and cite the exact lines they came from.
      </p>
      {starters.length > 0 && (
        <>
          <p className="mt-8 self-start text-xs font-medium text-slate-500">Try one of these</p>
          <ul className="mt-2 grid w-full gap-2.5 sm:grid-cols-2">
            {starters.map((q) => (
              <li key={q}>
                <button
                  onClick={() => onPick(q)}
                  className="card group flex h-full w-full items-start gap-3 px-4 py-3 text-left text-sm text-slate-700 transition hover:-translate-y-0.5 hover:border-brand-500/50 hover:shadow-lift"
                >
                  <Icon name="chat" className="mt-0.5 h-4 w-4 shrink-0 text-slate-400 group-hover:text-brand-link" />
                  <span className="flex-1">{q}</span>
                  <Icon name="arrow-right" className="mt-0.5 h-4 w-4 shrink-0 text-slate-300 opacity-0 transition group-hover:translate-x-0.5 group-hover:text-brand-link group-hover:opacity-100" />
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export function Composer({
  busy, onSend, onStop, maxChars, prefill,
}: { busy: boolean; onSend: (text: string) => void; onStop: () => void; maxChars: number; prefill?: string }) {
  const [draft, setDraft] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (prefill) setDraft(prefill);
  }, [prefill]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [draft]);

  useEffect(() => {
    if (!busy) ref.current?.focus();
  }, [busy]);

  const text = draft.trim();
  const send = () => {
    if (!text || busy) return;
    onSend(text);
    setDraft("");
  };

  return (
    <div className="px-4 pb-3 pt-2">
      <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-slate-300 bg-surface p-2 shadow-lift transition focus-within:border-brand-500 focus-within:ring-4 focus-within:ring-brand-500/15">
        <textarea
          ref={ref}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          rows={1}
          maxLength={maxChars}
          disabled={busy}
          placeholder={busy ? "Generating…" : "Ask something about your project…"}
          aria-label="Message"
          className="max-h-48 min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-[15px] leading-6 outline-none placeholder:text-slate-400 disabled:opacity-60"
        />
        {busy ? (
          <button onClick={onStop} aria-label="Stop generating" className="btn-secondary h-10 shrink-0 px-3.5">
            <Icon name="stop" className="h-3.5 w-3.5" /> Stop
          </button>
        ) : (
          <button onClick={send} disabled={!text} aria-label="Send" className="btn-primary h-10 w-10 shrink-0 rounded-xl px-0">
            <Icon name="send" className="h-4 w-4" />
          </button>
        )}
      </div>
      <div className="mx-auto mt-1.5 flex max-w-3xl items-center justify-between gap-3 px-1 text-xs text-slate-500">
        <span className="hidden sm:inline"><kbd className="kbd">Enter</kbd> to send · <kbd className="kbd">Shift</kbd>+<kbd className="kbd">Enter</kbd> for a new line</span>
        {draft.length > maxChars * 0.8 && (
          <span className={`ml-auto tabular-nums ${draft.length >= maxChars ? "text-red-600" : ""}`}>
            {draft.length.toLocaleString()} / {maxChars.toLocaleString()}
          </span>
        )}
      </div>
    </div>
  );
}

/** Dev-only (DEV_MODE=true): the exact prompt of the conversation's latest turn and its memory state. */
export function ContextInspectorDialog({
  data, loading, onClose,
}: { data: ContextInspection | undefined; loading: boolean; onClose: () => void }) {
  const turn = data?.turn;
  return (
    <Modal title="Context Inspector" onClose={onClose} wide>
      {loading && <p className="flex items-center gap-2 text-sm text-slate-500"><Spinner /> Loading…</p>}
      {data && !turn && <p className="text-sm text-slate-500">No turn has been generated in this session yet.</p>}
      {data && turn && (
        <div className="space-y-5 text-sm">
          <div className="flex flex-wrap gap-2">
            <Badge tone="brand">~{turn.prompt_tokens_estimate.toLocaleString()} prompt tokens</Badge>
            <Badge tone={turn.summary_used ? "success" : "neutral"}>{turn.summary_used ? "summary used" : "no summary yet"}</Badge>
            <Badge>
              memory: {data.recent_token_estimate.toLocaleString()} / {data.summary_threshold_tokens.toLocaleString()} tokens
            </Badge>
          </div>
          <div>
            <h3 className="font-semibold text-slate-800">Retrieval query</h3>
            <pre className="selectable-text mt-1 whitespace-pre-wrap rounded-lg bg-slate-100 p-3 font-mono text-xs">{turn.retrieval_query}</pre>
          </div>
          <div>
            <h3 className="font-semibold text-slate-800">Chunks sent ({turn.chunks.length})</h3>
            <ul className="mt-1 divide-y divide-slate-200 rounded-lg border border-slate-200">
              {turn.chunks.map((c, i) => (
                <li key={i} className="flex items-center justify-between gap-3 px-3 py-1.5 font-mono text-xs">
                  <span className="truncate">{c.file_path}{c.line_start != null ? `:${c.line_start}-${c.line_end}` : ""}</span>
                  <span className="shrink-0 text-slate-500">
                    {c.tokens} tok{c.vector_score != null ? ` · sim ${c.vector_score.toFixed(3)}` : " · bm25 only"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          {data.summary && (
            <div>
              <h3 className="font-semibold text-slate-800">Conversation summary</h3>
              <p className="selectable-text mt-1 whitespace-pre-wrap rounded-lg bg-slate-100 p-3">{data.summary}</p>
            </div>
          )}
          <div>
            <h3 className="font-semibold text-slate-800">Messages sent to the model</h3>
            <div className="mt-1 space-y-2">
              {turn.messages.map((m, i) => (
                <details key={i} className="rounded-lg border border-slate-200" open={i === turn.messages.length - 1}>
                  <summary className="cursor-pointer px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-600">
                    {m.role} · ~{Math.ceil(m.content.length / 4)} tokens
                  </summary>
                  <pre className="selectable-text max-h-72 overflow-auto whitespace-pre-wrap border-t border-slate-200 p-3 font-mono text-xs">{m.content}</pre>
                </details>
              ))}
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
