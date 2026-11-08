import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { chatApi } from "../../api/chat";
import AssistantMarkdown from "../chat/AssistantMarkdown";
import { CopyButton, Icon, Spinner } from "../ui";

type Phase = "thinking" | "streaming" | "done" | "stopped" | "error";

interface Props {
  fileId: string;
  path: string;
  question: string;
  line_start: number;
  line_end: number;
  /** Where the selection ended (viewport pixels): the panel opens just below it. */
  left: number;
  top: number;
  onClose: () => void;
  /** Continue the same question in the Chat tab (the only way an answer from here is saved). */
  onOpenInChat?: (question: string) => void;
}

/**
 * The small answer window on top of the file viewer: ask about the selected lines and read the streamed answer
 * without leaving the Files screen. Ephemeral: nothing is saved unless "Open in chat" is used.
 */
export default function SelectionAnswer({
  fileId, path, question: first, line_start, line_end, left, top, onClose, onOpenInChat,
}: Props) {
  const [question, setQuestion] = useState(first);
  const [text, setText] = useState("");
  const [phase, setPhase] = useState<Phase>("thinking");
  const [wait, setWait] = useState<number | null>(null);
  const [followUp, setFollowUp] = useState("");
  const [attempt, setAttempt] = useState(0);
  const abort = useRef<AbortController | null>(null);
  const range = line_start === line_end ? `line ${line_start}` : `lines ${line_start}-${line_end}`;

  useEffect(() => {
    const controller = new AbortController();
    abort.current = controller;
    let acc = "";
    setText("");
    setWait(null);
    setPhase("thinking");
    (async () => {
      try {
        for await (const ev of chatApi.askLines(fileId, question, { line_start, line_end }, controller.signal)) {
          if (ev.type === "waiting") setWait(ev.seconds);
          else if (ev.type === "token") { acc += ev.text; setText(acc); setWait(null); setPhase("streaming"); }
          else if (ev.type === "error") setPhase("error");
          else if (ev.type === "done") setPhase("done");
        }
      } catch {
        if (!controller.signal.aborted) setPhase("error");
      }
    })();
    return () => controller.abort();
  }, [fileId, question, line_start, line_end, attempt]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && (e.stopPropagation(), onClose());
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const busy = phase === "thinking" || phase === "streaming";
  const x = Math.max(8, Math.min(left, window.innerWidth - 440));
  const y = Math.max(8, Math.min(top + 8, window.innerHeight - 500));

  function stop() {
    abort.current?.abort();
    setPhase("stopped");
  }

  function ask(e: React.FormEvent) {
    e.preventDefault();
    const q = followUp.trim();
    if (!q || busy) return;
    setFollowUp("");
    setQuestion(q);
  }

  return createPortal(
    <div
      role="dialog"
      aria-label={`Answer about ${path}, ${range}`}
      style={{ left: x, top: y }}
      className="fixed z-[65] flex max-h-[min(70vh,30rem)] w-[26rem] max-w-[calc(100vw-1rem)] animate-scale-in flex-col overflow-hidden rounded-xl border border-slate-200 bg-surface shadow-pop"
    >
      <div className="flex items-center justify-between gap-2 border-b border-slate-200 px-3 py-2">
        <p className="min-w-0 truncate text-xs text-slate-500">
          <span className="font-mono text-slate-700">{path}</span> · {range}
        </p>
        <button onClick={onClose} aria-label="Close answer" title="Close (Esc)" className="icon-btn-sm -mr-1"><Icon name="x" className="h-3.5 w-3.5" /></button>
      </div>

      <div className="min-h-0 flex-1 select-text overflow-y-auto px-3 py-3">
        <p className="selectable-text mb-3 whitespace-pre-wrap break-words rounded-lg bg-slate-100 px-2.5 py-1.5 text-sm text-slate-700">{question}</p>
        {phase === "thinking" && (
          <p className="flex items-center gap-2 text-sm text-slate-500">
            <Spinner className="h-4 w-4" />
            {wait ? `Waiting for the model (~${wait}s)…` : "Reading these lines…"}
          </p>
        )}
        {text && <AssistantMarkdown text={text} />}
        {phase === "streaming" && <span className="mt-1 inline-block h-4 w-0.5 animate-pulse bg-brand-500" aria-hidden="true" />}
        {phase === "stopped" && <p className="mt-2 text-xs text-slate-500">Stopped.</p>}
        {phase === "error" && (
          <p role="alert" className="mt-2 flex items-center gap-2 text-sm text-red-600">
            Couldn't generate a response. Please try again.
            <button onClick={() => setAttempt((n) => n + 1)} className="btn-secondary px-2 py-1 text-xs">Retry</button>
          </p>
        )}
      </div>

      <div className="space-y-2 border-t border-slate-200 px-3 py-2">
        <form className="flex gap-2" onSubmit={ask}>
          <input
            value={followUp} onChange={(e) => setFollowUp(e.target.value)} maxLength={500} className="input py-1.5 text-sm"
            placeholder="Ask another question about these lines…" aria-label="Ask another question about the selected lines"
          />
          <button type="submit" disabled={busy || !followUp.trim()} className="btn-primary px-3 py-1.5" aria-label="Ask">
            <Icon name="send" />
          </button>
        </form>
        <div className="flex items-center gap-1">
          {busy && <button onClick={stop} className="btn-secondary px-2.5 py-1 text-xs"><Icon name="stop" className="h-3.5 w-3.5" /> Stop</button>}
          {text && !busy && <CopyButton text={text} className="text-slate-500" />}
          {onOpenInChat && (
            <button onClick={() => onOpenInChat(question)} className="btn-ghost ml-auto px-2.5 py-1 text-xs">
              <Icon name="chat" className="h-3.5 w-3.5" /> Open in chat
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
