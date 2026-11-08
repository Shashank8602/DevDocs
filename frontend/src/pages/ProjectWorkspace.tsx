import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { filesApi } from "../api/files";
import { projectsApi } from "../api/projects";
import { AskSelection } from "../api/chat";
import { reindexApi } from "../api/settings";
import { useAuth } from "../auth/AuthContext";
import AccessTab from "../components/access/AccessTab";
import ChatTab from "../components/chat/ChatTab";
import FilesTab from "../components/explorer/FilesTab";
import DashboardTab from "../components/explorer/DashboardTab";
import GraphTab from "../components/explorer/GraphTab";
import SearchTab from "../components/explorer/SearchTab";
import { Badge, EmptyState, Icon, Monogram, Skeleton, Spinner, useToast } from "../components/ui";

const FloatingFileViewer = lazy(() => import("../components/viewer/FloatingFileViewer"));

const RAIL_KEY = "devdocs-rail";
function storedRail(): boolean {
  try { return localStorage.getItem(RAIL_KEY) === "closed"; } catch { return false; }
}

const ALL_TABS = [
  { id: "Dashboard", icon: "dashboard" },
  { id: "Map", icon: "graph" },
  { id: "Files", icon: "folder" },
  { id: "Search", icon: "search" },
  { id: "Chat", icon: "chat" },
  { id: "Access", icon: "lock" },
] as const;
type Tab = (typeof ALL_TABS)[number]["id"];

const STATUS: Record<string, { label: string; tone: "success" | "brand" | "danger" | "warning" | "neutral" }> = {
  empty: { label: "Empty", tone: "neutral" },
  processing: { label: "Processing", tone: "brand" },
  ready: { label: "Ready", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
  needs_reindex: { label: "Re-index required", tone: "warning" },
};

function ReindexBanner({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [running, setRunning] = useState(false);
  const progress = useQuery({
    queryKey: ["projects", projectId, "reindex"],
    queryFn: () => reindexApi.progress(projectId),
    refetchInterval: running ? 1000 : false,
  });
  const begin = useMutation({
    mutationFn: () => reindexApi.begin(projectId),
    onSuccess: () => setRunning(true),
    onError: () => toast("Couldn't start the re-index. Please try again.", "error"),
  });
  const state = progress.data;

  useEffect(() => {
    if (!running || !state) return;
    if (state.state === "done") {
      setRunning(false);
      qc.invalidateQueries({ queryKey: ["projects"] });
      toast("Re-index finished. Chat is available again.", "success");
    } else if (state.state === "error") {
      setRunning(false);
      toast(state.message || "Re-indexing didn't finish. Try again.", "error");
    }
  }, [state, running, qc, toast]);

  const busy = running || state?.state === "running";
  const pct = state && state.total ? Math.round((state.done / state.total) * 100) : 0;
  return (
    <div className="mt-5 animate-slide-up overflow-hidden rounded-xl border border-amber-500/30 bg-amber-50">
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        <Icon name="alert" className="h-5 w-5 shrink-0 text-amber-700" />
        <div className="mr-auto min-w-0">
          <p className="text-sm font-semibold text-amber-700">Re-index required</p>
          <p className="text-sm text-amber-700/90">
            The embedding settings changed. Re-indexing re-embeds your stored chunks; you don't need to upload again.
          </p>
        </div>
        <button onClick={() => begin.mutate()} disabled={busy || begin.isPending} className="btn-primary">
          {busy ? <Spinner /> : <Icon name="refresh" />} {busy ? `Re-indexing… ${pct}%` : "Re-index"}
        </button>
      </div>
      {busy && (
        <div className="h-1 bg-amber-500/20">
          <div className="h-full bg-amber-500 transition-all duration-500" style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}

export default function ProjectWorkspace() {
  const { projectId = "" } = useParams();
  const { isAdmin } = useAuth();
  // The Access tab exists only for admins; for anyone else its address looks like any other missing tab.
  const TABS = ALL_TABS.filter((t) => t.id !== "Access" || isAdmin);
  const [params, setParams] = useSearchParams();
  const wanted = TABS.find((t) => t.id.toLowerCase() === (params.get("tab") === "overview" ? "dashboard" : params.get("tab"))?.toLowerCase())?.id;
  const [tab, setTabState] = useState<Tab>(wanted ?? "Dashboard");
  const [prefill, setPrefill] = useState<string | undefined>(undefined);
  const [viewing, setViewing] = useState<{ fileId: string; line?: number } | null>(null);
  const filesQuery = useQuery({ queryKey: ["projects", projectId, "files"], queryFn: () => filesApi.list(projectId) });
  const viewedFile = viewing ? filesQuery.data?.find((f) => f.id === viewing.fileId) : undefined;
  const [pendingAsk, setPendingAsk] = useState<{ question: string; selection: AskSelection } | null>(null);
  const askAbout = (question: string, selection: AskSelection) => {
    setViewing(null);
    setPendingAsk({ question, selection });
    setTab("Chat");
  };
  const ask = (question: string) => {
    setPrefill(question);
    setTab("Chat");
  };
  const setTab = (t: Tab) => {
    setTabState(t);
    setChatRailOpen(false);
    setParams({ tab: t.toLowerCase() }, { replace: true });
  };
  // The sidebar folds to an icon rail: always on request, and by default on Chat (which has its own conversation list).
  const [railClosed, setRailClosed] = useState(storedRail);
  const [chatRailOpen, setChatRailOpen] = useState(false);
  const collapsed = tab === "Chat" ? !chatRailOpen : railClosed;
  const toggleRail = () => {
    if (tab === "Chat") return setChatRailOpen(collapsed);
    const next = !railClosed;
    setRailClosed(next);
    try { localStorage.setItem(RAIL_KEY, next ? "closed" : "open"); } catch { /* per-session only */ }
  };
  const { data: active = [] } = useQuery({
    queryKey: ["ingestion", "active"],
    queryFn: filesApi.activeIngestion,
    refetchInterval: 2000,
  });
  const { data: overview } = useQuery({
    queryKey: ["projects", projectId, "overview"],
    queryFn: () => filesApi.overview(projectId),
  });
  const { data: project, isLoading, isError } = useQuery({
    queryKey: ["projects", projectId],
    queryFn: () => projectsApi.get(projectId),
    refetchInterval: (q) => (q.state.data?.status === "processing" ? 2000 : false),
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" /><Skeleton className="h-4 w-72" /><Skeleton className="mt-6 h-64" />
      </div>
    );
  }
  if (isError || !project) {
    return (
      <EmptyState
        icon="alert" title="This project doesn't exist."
        action={<Link to="/" className="btn-secondary">Back to projects</Link>}
      />
    );
  }

  const isEmpty = project.file_count === 0;
  const paused = project.status === "processing" && project.paused_until ? new Date(project.paused_until) : null;
  const ingest = active.find((a) => a.project_id === projectId);
  const status = STATUS[project.status] ?? { label: project.status, tone: "neutral" as const };

  const pct = ingest?.total ? ((ingest.total - ingest.remaining) / ingest.total) * 100 : 0;
  // labels stay readable to screen readers when the rail is folded
  const railLabel = collapsed ? "md:sr-only" : "";

  return (
    <section className="flex h-full min-h-0 flex-col md:flex-row">
      <nav
        role="tablist"
        aria-orientation="vertical"
        aria-label="Project sections"
        className={`flex shrink-0 flex-col border-b border-slate-200 bg-surface/60 transition-[width] duration-200 md:border-b-0 md:border-r ${collapsed ? "md:w-16" : "md:w-60"}`}
      >
        <div className={`p-4 pb-3 ${collapsed ? "md:px-3" : ""}`}>
          <div className={`flex items-start gap-3 ${collapsed ? "md:flex-col md:items-center" : ""}`}>
            <Monogram name={project.name} className="h-10 w-10 rounded-xl text-sm" />
            <div className={`min-w-0 flex-1 ${collapsed ? "md:hidden" : ""}`}>
              <h1 className="truncate text-base font-semibold leading-6 tracking-tight" title={project.name}>{project.name}</h1>
              <div className="mt-0.5"><Badge tone={status.tone} dot>{status.label}</Badge></div>
            </div>
            {collapsed && (
              <span className={`hidden h-2 w-2 rounded-full md:block ${
                { success: "bg-emerald-500", brand: "bg-brand-500", danger: "bg-red-500", warning: "bg-amber-500", neutral: "bg-slate-400" }[status.tone]
              }`} title={status.label} />
            )}
          </div>
          {project.description && <p className={`mt-3 line-clamp-3 text-xs leading-5 text-slate-500 ${collapsed ? "md:hidden" : ""}`}>{project.description}</p>}
          {ingest && (
            <div className="mt-3" role="status" aria-label="Ingestion progress" title={`Processing ${ingest.total - ingest.remaining} of ${ingest.total} files`}>
              <div className={`flex justify-between text-xs text-slate-600 ${collapsed ? "md:hidden" : ""}`}>
                <span>Processing files</span>
                <span className="tabular-nums">{ingest.total - ingest.remaining} / {ingest.total}</span>
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-200">
                <div className="h-full rounded-full bg-brand-600 transition-all duration-500" style={{ width: `${pct}%` }} />
              </div>
            </div>
          )}
        </div>
        <div className={`flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:overflow-visible ${collapsed ? "md:items-center" : ""}`}>
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              title={collapsed ? t.id : undefined}
              className={`flex shrink-0 items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-brand-500/40 ${
                collapsed ? "md:h-10 md:w-10 md:justify-center md:p-0" : ""
              } ${
                tab === t.id
                  ? "bg-brand-500/10 text-brand-link"
                  : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
              }`}
            >
              <Icon name={t.icon} className="h-[18px] w-[18px] shrink-0" />
              <span className={railLabel}>{t.id}</span>
            </button>
          ))}
        </div>
        <div className={`mt-auto hidden border-t border-slate-200 md:flex ${collapsed ? "flex-col items-center gap-1 px-2 py-2" : "items-center gap-2 px-3 py-2.5"}`}>
          {!collapsed && (
            <p className="min-w-0 flex-1 text-xs leading-5 text-slate-500">
              <span className="flex items-center gap-1.5"><Icon name="clock" className="h-3.5 w-3.5" /> Last updated</span>
              <span className="block truncate text-slate-700">{overview?.last_updated ? new Date(overview.last_updated).toLocaleString() : "—"}</span>
            </p>
          )}
          <button
            type="button" onClick={toggleRail} className="icon-btn"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            <Icon name="panel-left" />
          </button>
        </div>
      </nav>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {paused && (
          <div role="status" className="shrink-0 px-6 pt-4">
            <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-50 px-4 py-3 text-sm text-amber-700">
              <Icon name="clock" className="mt-0.5 h-5 w-5 shrink-0" />
              <div>
                <p className="font-semibold">Waiting for the embedding quota</p>
                <p className="text-amber-700/90">
                  Nothing failed. The remaining files stay queued and processing resumes by itself{" "}
                  {project.paused_daily_quota ? "when the daily quota is back (checked every 30 minutes)" : `at about ${paused.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}.
                </p>
              </div>
            </div>
          </div>
        )}
        {project.status === "needs_reindex" && (
          <div className="shrink-0 px-6 pb-1"><ReindexBanner projectId={projectId} /></div>
        )}
        <div
          key={tab}
          className={`min-h-0 flex-1 animate-fade-in ${tab === "Chat" ? "[&>.card]:m-6" : "overflow-y-auto p-6"}`}
        >
          {tab === "Files" ? (
            <FilesTab projectId={projectId} processing={project.status === "processing"} onAsk={askAbout} />
          ) : tab === "Dashboard" && isEmpty ? (
            <EmptyState
              icon="folder" title="No files yet"
              action={<button onClick={() => setTab("Files")} className="btn-primary">Go to Files</button>}
            >
              No files have been added yet. Upload your project folder to get started.
            </EmptyState>
          ) : tab === "Dashboard" ? (
            <DashboardTab projectId={projectId} onAsk={ask} />
          ) : tab === "Map" ? (
            <GraphTab projectId={projectId} onAsk={ask} onOpenFile={(fileId) => setViewing({ fileId })} />
          ) : tab === "Search" ? (
            <SearchTab projectId={projectId} onAsk={ask} onOpenFile={(fileId, line) => setViewing({ fileId, line })} />
          ) : tab === "Access" && isAdmin ? (
            <AccessTab projectId={projectId} />
          ) : tab === "Chat" ? (
            <ChatTab projectId={projectId} onUpload={() => setTab("Files")} needsReindex={project.status === "needs_reindex"} prefill={prefill} pendingAsk={pendingAsk} onAskHandled={() => setPendingAsk(null)} />
          ) : null}
        </div>
      </div>
      {viewedFile && viewing && (
        <Suspense fallback={null}>
          <FloatingFileViewer
            file={viewedFile}
            highlight={viewing.line ? [[viewing.line, viewing.line]] : undefined}
            onClose={() => setViewing(null)}
            onAsk={askAbout}
          />
        </Suspense>
      )}
    </section>
  );
}
