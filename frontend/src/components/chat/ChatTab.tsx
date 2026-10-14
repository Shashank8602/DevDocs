import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { ChatEvent, ChatMessage, chatApi, ContextInspection, ConversationSummary, SourceRef } from "../../api/chat";
import { filesApi, ProjectFile } from "../../api/files";
const FloatingFileViewer = lazy(() => import("../viewer/FloatingFileViewer"));
import Modal from "../Modal";
import { EmptyState, Icon, Skeleton, Spinner, useToast } from "../ui";
import AssistantMarkdown from "./AssistantMarkdown";
import {
  AssistantAvatar, BlankState, Composer, ContextInspectorDialog, MessageView, StoppedBar, ThinkingIndicator,
} from "./ChatParts";

const MAX_CHARS = 8000;

type Phase = "idle" | "thinking" | "streaming";
type Problem = { kind: "stopped" | "failed"; messageId: string | null } | null;

function ago(iso: string) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** The unanswered question at the end of a restored conversation, if any (rebuilt from `gen_state`). */
function problemOf(messages: ChatMessage[]): Problem {
  const last = messages[messages.length - 1];
  if (!last || last.role !== "user") return null;
  if (last.gen_state === "stopped") return { kind: "stopped", messageId: last.id };
  if (last.gen_state === "failed" || last.gen_state === "pending") return { kind: "failed", messageId: last.id };
  return null;
}

function Sidebar({
  items, activeId, loading, disabled, onPick, onNew, onDelete,
}: {
  items: ConversationSummary[]; activeId: string | null; loading: boolean; disabled: boolean;
  onPick: (id: string) => void; onNew: () => void; onDelete: (c: ConversationSummary) => void;
}) {
  return (
    <aside className="flex w-full shrink-0 flex-col border-b border-slate-200 bg-slate-50/60 md:w-64 md:border-b-0 md:border-r">
      <div className="p-3">
        <button onClick={onNew} disabled={disabled} className="btn-primary w-full">
          <Icon name="plus" /> New Chat
        </button>
      </div>
      <nav className="max-h-48 min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3 md:max-h-none" aria-label="Conversations">
        {loading && <><Skeleton className="mx-1 h-9" /><Skeleton className="mx-1 mt-1 h-9" /></>}
        {!loading && items.length === 0 && <p className="px-3 py-2 text-xs text-slate-500">No conversations yet.</p>}
        {items.map((c) => (
          <div
            key={c.id}
            className={`group flex items-center gap-1 rounded-lg transition ${
              c.id === activeId ? "bg-brand-500/10 text-slate-900" : "text-slate-700 hover:bg-slate-100"
            }`}
          >
            <button
              onClick={() => onPick(c.id)}
              disabled={disabled}
              aria-current={c.id === activeId ? "true" : undefined}
              className="min-w-0 flex-1 px-3 py-2 text-left disabled:opacity-60"
            >
              <span className="block truncate text-sm font-medium">{c.title}</span>
              <span className="block text-[11px] text-slate-500">{ago(c.updated_at)}</span>
            </button>
            <button
              onClick={() => onDelete(c)}
              aria-label={`Delete conversation ${c.title}`}
              className="mr-1.5 rounded-md p-1.5 text-slate-400 opacity-0 transition hover:bg-red-100 hover:text-red-600 focus:opacity-100 group-hover:opacity-100"
            >
              <Icon name="trash" className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </nav>
    </aside>
  );
}

export default function ChatTab({
  projectId, onUpload, needsReindex = false,
}: { projectId: string; onUpload: () => void; needsReindex?: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [phase, setPhase] = useState<Phase>("idle");
  const [streamed, setStreamed] = useState("");
  const [partial, setPartial] = useState(""); // what was shown when a turn was stopped or failed (never saved)
  const [waitSeconds, setWaitSeconds] = useState<number | null>(null);
  const [problem, setProblem] = useState<Problem>(null);
  const [restored, setRestored] = useState(false);
  const [opening, setOpening] = useState(false);
  const [viewing, setViewing] = useState<{ file: ProjectFile; highlight: [number, number][] } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<ConversationSummary | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const stoppedByUser = useRef(false);
  const busy = phase !== "idle";

  const { data: files, isLoading: filesLoading } = useQuery({
    queryKey: ["projects", projectId, "files"],
    queryFn: () => filesApi.list(projectId),
  });
  const { data: config } = useQuery({ queryKey: ["config"], queryFn: filesApi.config, staleTime: Infinity });
  const { data: conversations = [], isLoading: listLoading } = useQuery({
    queryKey: ["projects", projectId, "conversations"],
    queryFn: () => chatApi.conversations(projectId),
  });
  const { data: inspection, isFetching: inspecting_ } = useQuery<ContextInspection>({
    queryKey: ["context", conversationId, inspecting],
    queryFn: () => chatApi.context(conversationId as string),
    enabled: inspecting && conversationId !== null,
    gcTime: 0,
  });

  const refreshList = useCallback(
    () => qc.invalidateQueries({ queryKey: ["projects", projectId, "conversations"] }),
    [qc, projectId],
  );

  const open = useCallback(async (id: string) => {
    setOpening(true);
    try {
      const detail = await chatApi.conversation(id);
      setConversationId(detail.id);
      setMessages(detail.messages);
      setProblem(problemOf(detail.messages));
      setPartial("");
    } catch {
      toast("Couldn't open that conversation.", "error");
    } finally {
      setOpening(false);
    }
  }, [toast]);

  // On first load restore the most recent conversation from the database (no LLM call).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await chatApi.conversations(projectId);
        if (list.length && !cancelled) await open(list[0].id);
      } catch {
        /* a blank chat is a fine fallback */
      } finally {
        if (!cancelled) setRestored(true);
      }
    })();
    return () => { cancelled = true; };
  }, [projectId, open]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end", behavior: phase === "streaming" ? "auto" : "smooth" });
  }, [messages, streamed, phase, problem]);

  async function run(openStream: (signal: AbortSignal) => AsyncGenerator<ChatEvent>) {
    const controller = new AbortController();
    abortRef.current = controller;
    stoppedByUser.current = false;
    setProblem(null);
    setPartial("");
    setStreamed("");
    setWaitSeconds(null);
    setPhase("thinking");
    let acc = "";
    let cid = conversationId;
    let userMessageId: string | null = null;
    let finished = false;
    try {
      for await (const ev of openStream(controller.signal)) {
        if (ev.type === "conversation") {
          cid = ev.conversation_id;
          userMessageId = ev.user_message_id;
          setConversationId(ev.conversation_id);
        } else if (ev.type === "waiting") {
          setWaitSeconds(ev.seconds);
        } else if (ev.type === "token") {
          acc += ev.text;
          setWaitSeconds(null);
          setStreamed(acc);
          setPhase("streaming");
        } else if (ev.type === "done" || ev.type === "no_context") {
          finished = true;
        } else if (ev.type === "error") {
          setProblem({ kind: "failed", messageId: userMessageId });
        }
      }
    } catch {
      if (stoppedByUser.current) setProblem({ kind: "stopped", messageId: userMessageId });
      else setProblem({ kind: "failed", messageId: userMessageId });
    } finally {
      if (stoppedByUser.current && !finished) setProblem({ kind: "stopped", messageId: userMessageId });
      abortRef.current = null;
      if (!finished) setPartial(acc);
      setPhase("idle");
      setWaitSeconds(null);
      if (cid) {
        // Authoritative copy from the database: real ids, frozen sources, the title.
        try {
          const detail = await chatApi.conversation(cid);
          setMessages(detail.messages);
        } catch { /* keep what is on screen */ }
      }
      setStreamed("");
      refreshList();
    }
  }

  function send(text: string) {
    const content = text.trim();
    if (!content || busy) return;
    setMessages((m) => [...m, {
      id: `local-${Date.now()}`, role: "user", content, sources_json: null, gen_state: "pending",
      created_at: new Date().toISOString(),
    }]);
    void run((signal) => (conversationId ? chatApi.send(conversationId, content, signal) : chatApi.start(projectId, content, signal)));
  }

  function stop() {
    stoppedByUser.current = true;
    abortRef.current?.abort();
  }

  function retry() {
    if (!problem?.messageId || busy) return;
    const id = problem.messageId;
    void run((signal) => chatApi.retry(id, signal));
  }

  async function cancel() {
    if (problem?.messageId) await chatApi.dismiss(problem.messageId);
    setProblem(null);
    setPartial("");
  }

  function newChat() {
    if (busy) return;
    setConversationId(null);
    setMessages([]);
    setProblem(null);
    setPartial("");
  }

  async function confirmRemove() {
    const target = confirmDelete;
    if (!target) return;
    try {
      await chatApi.remove(target.id);
      setConfirmDelete(null);
      const list = await chatApi.conversations(projectId);
      qc.setQueryData(["projects", projectId, "conversations"], list);
      if (target.id === conversationId) {
        if (list.length) await open(list[0].id);
        else newChat();
      }
    } catch {
      toast("Couldn't delete the conversation.", "error");
      setConfirmDelete(null);
    }
  }

  function openSource(s: SourceRef) {
    const file = files?.find((f) => f.id === s.file_id);
    if (!file) {
      toast("This file doesn't exist anymore.", "error");
      return;
    }
    setViewing({ file, highlight: s.ranges });
  }

  const total = files?.length ?? 0;
  const ready = files?.filter((f) => f.status === "ready").length ?? 0;
  const failed = files?.filter((f) => f.status === "failed").length ?? 0;

  if (filesLoading || !restored) {
    return (
      <div className="card space-y-3 p-6">
        <Skeleton className="h-5 w-1/3" /><Skeleton className="h-4 w-2/3" /><Skeleton className="h-4 w-1/2" />
      </div>
    );
  }
  if (total === 0) {
    return (
      <EmptyState
        icon="folder" title="No files yet"
        action={<button onClick={onUpload} className="btn-primary">Upload Project</button>}
      >
        No files have been added yet. Upload your project folder to get started.
      </EmptyState>
    );
  }
  if (needsReindex) {
    return (
      <EmptyState icon="layers" title="Re-index required">
        The embedding settings changed, so this project's search index is out of date. Re-index it from the banner
        above and chat will be available again.
      </EmptyState>
    );
  }
  if (ready === 0) {
    return failed === total ? (
      <EmptyState icon="alert" title="Nothing to chat with yet">
        No files are ready. Some files failed to process. Retry the failed files to start chatting.
      </EmptyState>
    ) : (
      <EmptyState icon="chat" title="Getting your files ready">No files are ready yet.</EmptyState>
    );
  }

  const blank = messages.length === 0 && !busy && !opening;

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden md:flex-row">
      <Sidebar
        items={conversations} activeId={conversationId} loading={listLoading} disabled={busy}
        onPick={(id) => id !== conversationId && void open(id)} onNew={newChat} onDelete={setConfirmDelete}
      />

      <section className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex h-11 shrink-0 items-center justify-between border-b border-slate-200 px-4">
          <h2 className="truncate text-sm font-semibold text-slate-800">
            {conversations.find((c) => c.id === conversationId)?.title ?? "New chat"}
          </h2>
          {config?.dev_mode && conversationId && (
            <button onClick={() => setInspecting(true)} className="btn-ghost px-2 py-1 text-xs" title="Dev: show the exact prompt of the last turn">
              <Icon name="bug" className="h-3.5 w-3.5" /> Context
            </button>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto" aria-live="polite">
          <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
            {opening && <div className="flex justify-center py-10 text-slate-400"><Spinner className="h-5 w-5" /></div>}
            {blank && <BlankState onPick={send} />}
            {!opening && messages.map((m) => <MessageView key={m.id} m={m} onOpenSource={openSource} />)}

            {busy && (
              <div className="flex animate-slide-up gap-3">
                <AssistantAvatar />
                <div className="min-w-0 flex-1">
                  {phase === "thinking" ? (
                    <ThinkingIndicator waitSeconds={waitSeconds} />
                  ) : (
                    <>
                      <AssistantMarkdown text={streamed} />
                      <span className="mt-1 inline-block h-4 w-0.5 animate-pulse bg-brand-500" aria-hidden="true" />
                    </>
                  )}
                </div>
              </div>
            )}

            {!busy && problem && partial && (
              <div className="flex gap-3 opacity-80">
                <AssistantAvatar />
                <div className="min-w-0 flex-1"><AssistantMarkdown text={partial} /></div>
              </div>
            )}
            {!busy && problem && (
              <StoppedBar kind={problem.kind} onRetry={retry} onCancel={cancel} />
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        <Composer busy={busy} onSend={send} onStop={stop} maxChars={MAX_CHARS} />
      </section>

      {viewing && (
        <Suspense fallback={null}>
          <FloatingFileViewer file={viewing.file} highlight={viewing.highlight} onClose={() => setViewing(null)} />
        </Suspense>
      )}

      {confirmDelete && (
        <Modal title="Delete conversation?" onClose={() => setConfirmDelete(null)}>
          <p className="text-sm text-slate-600">
            “{confirmDelete.title}” and its messages will be removed. Your project's files are not affected.
          </p>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setConfirmDelete(null)} className="btn-secondary">Cancel</button>
            <button onClick={confirmRemove} className="btn-danger">Delete</button>
          </div>
        </Modal>
      )}

      {inspecting && (
        <ContextInspectorDialog data={inspection} loading={inspecting_} onClose={() => setInspecting(false)} />
      )}
    </div>
  );
}
