import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { filesApi } from "../api/files";
import { projectsApi } from "../api/projects";
import { reindexApi } from "../api/settings";
import ChatTab from "../components/chat/ChatTab";
import FilesTab from "../components/explorer/FilesTab";
import OverviewTab from "../components/explorer/OverviewTab";
import { Badge, EmptyState, Icon, Skeleton, Spinner, useToast } from "../components/ui";

const TABS = [
  { id: "Overview", icon: "layers" },
  { id: "Files", icon: "file" },
  { id: "Chat", icon: "chat" },
] as const;
type Tab = (typeof TABS)[number]["id"];

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
  const [params, setParams] = useSearchParams();
  const wanted = TABS.find((t) => t.id.toLowerCase() === params.get("tab")?.toLowerCase())?.id;
  const [tab, setTabState] = useState<Tab>(wanted ?? "Overview");
  const setTab = (t: Tab) => {
    setTabState(t);
    setParams({ tab: t.toLowerCase() }, { replace: true });
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
  const ingest = active.find((a) => a.project_id === projectId);
  const status = STATUS[project.status] ?? { label: project.status, tone: "neutral" as const };

  return (
    <section className="flex h-full min-h-0 flex-col md:flex-row">
      <nav
        role="tablist"
        aria-orientation="vertical"
        className="flex shrink-0 flex-col border-b border-slate-200 bg-surface/60 md:w-60 md:border-b-0 md:border-r"
      >
        <div className="p-4 pb-3">
          <Link to="/" className="btn-secondary w-full justify-start pl-2.5">
            <Icon name="chevron-left" className="h-4 w-4" /> All projects
          </Link>
          <h1 className="mt-3 truncate text-lg font-semibold tracking-tight" title={project.name}>{project.name}</h1>
          <div className="mt-1.5"><Badge tone={status.tone} dot>{status.label}</Badge></div>
          {project.description && <p className="mt-2 line-clamp-3 text-xs text-slate-500">{project.description}</p>}
          {ingest && (
            <div className="mt-3" role="status" aria-label="Ingestion progress">
              <div className="flex justify-between text-xs text-slate-600">
                <span>Processing files</span>
                <span className="tabular-nums">{ingest.total - ingest.remaining} / {ingest.total}</span>
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-200">
                <div className="h-full rounded-full bg-brand-600 transition-all duration-500" style={{ width: `${ingest.total ? ((ingest.total - ingest.remaining) / ingest.total) * 100 : 0}%` }} />
              </div>
            </div>
          )}
        </div>
        <div className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:overflow-visible">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-sm font-medium outline-none transition-colors focus-visible:ring-1 focus-visible:ring-brand-500/40 ${
                tab === t.id
                  ? "border-brand-500/25 bg-brand-500/10 text-brand-link"
                  : "border-transparent text-slate-600 hover:bg-slate-100 hover:text-slate-900"
              }`}
            >
              <Icon name={t.icon} className="h-4 w-4" />
              {t.id}
            </button>
          ))}
        </div>
        <p className="mt-auto hidden border-t border-slate-200 px-4 py-3 text-xs text-slate-500 md:block">
          Last updated<br />
          <span className="text-slate-700">{overview?.last_updated ? new Date(overview.last_updated).toLocaleString() : "—"}</span>
        </p>
      </nav>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {project.status === "needs_reindex" && (
          <div className="shrink-0 px-6 pb-1"><ReindexBanner projectId={projectId} /></div>
        )}
        <div
          key={tab}
          className={`min-h-0 flex-1 animate-fade-in ${tab === "Chat" ? "[&>.card]:m-6" : "overflow-y-auto p-6"}`}
        >
          {tab === "Files" ? (
            <FilesTab projectId={projectId} processing={project.status === "processing"} />
          ) : tab === "Overview" && isEmpty ? (
            <EmptyState
              icon="folder" title="No files yet"
              action={<button onClick={() => setTab("Files")} className="btn-primary">Go to Files</button>}
            >
              No files have been added yet. Upload your project folder to get started.
            </EmptyState>
          ) : tab === "Overview" ? (
            <OverviewTab projectId={projectId} />
          ) : tab === "Chat" ? (
            <ChatTab projectId={projectId} onUpload={() => setTab("Files")} needsReindex={project.status === "needs_reindex"} />
          ) : null}
        </div>
      </div>
    </section>
  );
}
