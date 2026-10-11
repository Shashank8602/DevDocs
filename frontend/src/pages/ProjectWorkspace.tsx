import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { projectsApi } from "../api/projects";
import FilesTab from "../components/explorer/FilesTab";
import OverviewTab from "../components/explorer/OverviewTab";

const TABS = ["Overview", "Files", "Chat"] as const;
type Tab = (typeof TABS)[number];

const STATUS_LABEL: Record<string, string> = {
  empty: "Empty",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
  needs_reindex: "Needs re-index",
};

export default function ProjectWorkspace() {
  const { projectId = "" } = useParams();
  const [tab, setTab] = useState<Tab>("Overview");
  const { data: project, isLoading, isError } = useQuery({
    queryKey: ["projects", projectId],
    queryFn: () => projectsApi.get(projectId),
    refetchInterval: (q) => (q.state.data?.status === "processing" ? 2000 : false),
  });

  if (isLoading) return <p className="text-slate-500">Loading…</p>;
  if (isError || !project) {
    return (
      <div>
        <p className="text-slate-700">This project doesn't exist.</p>
        <Link to="/" className="mt-2 inline-block text-sm text-brand-link hover:underline">Back to projects</Link>
      </div>
    );
  }

  const isEmpty = project.file_count === 0;

  return (
    <section>
      <Link
        to="/"
        className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-surface py-1.5 pl-2.5 pr-3 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-100 active:scale-95"
      >
        <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4" aria-hidden="true">
          <path fillRule="evenodd" d="M12.7 5.3a1 1 0 0 1 0 1.4L9.4 10l3.3 3.3a1 1 0 0 1-1.4 1.4l-4-4a1 1 0 0 1 0-1.4l4-4a1 1 0 0 1 1.4 0Z" clipRule="evenodd" />
        </svg>
        All projects
      </Link>
      <div className="mt-4 flex items-center gap-3">
        <h1 className="text-2xl font-semibold">{project.name}</h1>
        <span className="rounded-full bg-slate-200 px-2.5 py-0.5 text-xs font-medium text-slate-700">
          {STATUS_LABEL[project.status] ?? project.status}
        </span>
      </div>
      {project.description && <p className="mt-1 text-sm text-slate-500">{project.description}</p>}

      <div role="tablist" className="mt-6 flex gap-6 border-b border-slate-200">
        {TABS.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`-mb-px border-b-2 pb-2 text-sm font-medium ${
              tab === t ? "border-brand-600 text-brand-link" : "border-transparent text-slate-500 hover:text-slate-900"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="mt-8">
        {tab === "Files" ? (
          <FilesTab projectId={projectId} processing={project.status === "processing"} />
        ) : tab === "Overview" && isEmpty ? (
          <div className="rounded-lg border border-dashed border-slate-300 bg-surface p-12 text-center">
            <p className="text-slate-600">No files have been added yet. Upload your project folder to get started.</p>
            <button
              onClick={() => setTab("Files")}
              className="mt-4 rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500"
            >
              Go to Files
            </button>
          </div>
        ) : tab === "Overview" ? (
          <OverviewTab projectId={projectId} />
        ) : (
          <p className="text-slate-500">{tab} is coming soon.</p>
        )}
      </div>
    </section>
  );
}
