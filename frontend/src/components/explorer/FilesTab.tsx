import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { filesApi, ProjectFile, UploadReport } from "../../api/files";
import { useAuth } from "../../auth/AuthContext";
import { readDropped } from "../../lib/dropFolder";
import { filterSelection, FilterResult, fromInput, REASON_LABEL, Selected } from "../../lib/ignore";
import { buildTree, searchFiles, TreeNode } from "../../lib/tree";
import Modal from "../Modal";
import { Badge, FileIcon, Icon, Skeleton, Spinner } from "../ui";
import UploadReportView from "./UploadReportView";
import ChunkInspector from "./ChunkInspector";
import ESInspector from "./ESInspector";
import RetrievalPlayground from "./RetrievalPlayground";
const FloatingFileViewer = lazy(() => import("../viewer/FloatingFileViewer"));

const STATUS_LABEL: Record<string, string> = { pending: "Pending", processing: "Processing", ready: "Ready", failed: "Failed" };

interface RowActions {
  onOpen: (f: ProjectFile) => void;
  onDelete: (f: ProjectFile) => void;
  onReplace: (f: ProjectFile) => void;
  onRetry: (f: ProjectFile) => void;
  onChunks?: (f: ProjectFile) => void; // dev mode only
  locked: boolean; // the project is ingesting: replacing is not allowed
  readOnly: boolean; // members read files; only admins replace, delete or retry them
}

function StatusBadge({ file, actions }: { file: ProjectFile; actions: RowActions }) {
  const [open, setOpen] = useState(false);
  // Ready is the normal state: a quiet dot keeps a long list calm; anything else gets a labelled badge.
  if (file.status === "ready") {
    return (
      <span className="mx-2 flex h-5 w-5 shrink-0 items-center justify-center" title="Ready">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
        <span className="sr-only">Ready</span>
      </span>
    );
  }
  if (file.status !== "failed" || actions.readOnly) {
    return (
      <span className={`ml-2 shrink-0 ${file.status === "processing" ? "animate-pulse" : ""}`}>
        <Badge tone={file.status === "failed" ? "danger" : file.status === "processing" ? "brand" : "neutral"} dot>
          {STATUS_LABEL[file.status] ?? file.status}
        </Badge>
      </span>
    );
  }
  return (
    <span className="relative ml-2 shrink-0">
      <button
        onClick={() => setOpen(!open)} aria-expanded={open}
        className="inline-flex items-center gap-1.5 rounded-full bg-red-500/15 px-2.5 py-0.5 text-xs font-medium text-red-700 transition hover:bg-red-500/25"
      >
        <Icon name="alert" className="h-3 w-3" /> Failed
      </button>
      {open && (
        <div role="dialog" aria-label="Processing failed" className="absolute right-0 top-full z-30 mt-1.5 w-72 animate-scale-in rounded-xl border border-slate-200 bg-surface p-3.5 shadow-lift">
          <p className="flex items-start gap-2 text-sm text-slate-700">
            <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0 text-red-600" />
            DevDocs couldn't process this file.
          </p>
          <div className="mt-3 flex justify-end gap-2">
            <button onClick={() => { setOpen(false); actions.onRetry(file); }} className="btn-secondary px-3 py-1.5">
              <Icon name="refresh" className="h-3.5 w-3.5" /> Retry
            </button>
            <button onClick={() => { setOpen(false); actions.onReplace(file); }} disabled={actions.locked} className="btn-primary px-3 py-1.5">
              <Icon name="upload" className="h-3.5 w-3.5" /> Replace File
            </button>
          </div>
        </div>
      )}
    </span>
  );
}

function FileRow({
  file, label, sublabel, actions,
}: { file: ProjectFile; label: string; sublabel?: string; actions: RowActions }) {
  const hover = "icon-btn-sm opacity-0 focus:opacity-100 group-hover:opacity-100";
  return (
    <div className="group flex items-center rounded-lg py-0.5 pl-2 pr-1 transition-colors hover:bg-slate-100">
      <button onClick={() => actions.onOpen(file)} className="flex min-w-0 flex-1 items-center gap-2.5 py-1 text-left text-sm text-slate-800" title={file.file_path}>
        <FileIcon name={file.file_name} />
        <span className="min-w-0">
          <span className="block truncate group-hover:text-slate-900">{label}</span>
          {sublabel && <span className="block truncate font-mono text-xs text-slate-500">{sublabel}</span>}
        </span>
      </button>
      <StatusBadge file={file} actions={actions} />
      {actions.onChunks && (
        <button onClick={() => actions.onChunks!(file)} className={`${hover} hover:text-brand-link`} aria-label={`Chunks of ${file.file_name}`} title="Chunks (dev)">
          <Icon name="layers" className="h-3.5 w-3.5" />
        </button>
      )}
      {!actions.readOnly && (
        <>
          <button
            onClick={() => actions.onReplace(file)} disabled={actions.locked}
            className={`${hover} hover:text-brand-link`} aria-label={`Replace ${file.file_name}`} title="Replace file"
          >
            <Icon name="upload" className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => actions.onDelete(file)}
            aria-label={`Delete ${file.file_name}`} title="Delete file"
            className={`${hover} hover:bg-red-50 hover:text-red-600`}
          >
            <Icon name="trash" className="h-3.5 w-3.5" />
          </button>
        </>
      )}
    </div>
  );
}

function TreeFolder({ node, depth, actions }: { node: TreeNode; depth: number; actions: RowActions }) {
  const [open, setOpen] = useState(depth < 1);
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100"
      >
        <Icon name="chevron-right" className={`h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform ${open ? "rotate-90" : ""}`} />
        <Icon name={open ? "folder-open" : "folder"} className="h-4 w-4 shrink-0 text-amber-500" />
        <span className="truncate">{node.name}</span>
      </button>
      {open && (
        <div className="ml-[0.95rem] border-l border-slate-200 pl-2">
          {node.children.map((c) =>
            c.file ? (
              <FileRow key={c.path} file={c.file} label={c.name} actions={actions} />
            ) : (
              <TreeFolder key={c.path} node={c} depth={depth + 1} actions={actions} />
            ),
          )}
        </div>
      )}
    </div>
  );
}

export default function FilesTab({
  projectId, processing, onAsk,
}: { projectId: string; processing: boolean; onAsk?: (question: string, selection: { file_id: string; line_start: number; line_end: number }) => void }) {
  const qc = useQueryClient();
  const { isAdmin } = useAuth();
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [viewing, setViewing] = useState<ProjectFile | null>(null);
  const [deleting, setDeleting] = useState<ProjectFile | null>(null);
  const [staged, setStaged] = useState<FilterResult | null>(null);
  const [message, setMessage] = useState<{ title: string; body?: string; report?: UploadReport; skipped?: { count: number; reasons: string[] }; flagged?: { path: string; detail: string | null }[] } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [scanning, setScanning] = useState(false);

  const { data: files = [], isLoading } = useQuery({
    queryKey: ["projects", projectId, "files"],
    queryFn: () => filesApi.list(projectId),
    // Statuses change in the background while the worker ingests, so poll until everything has settled.
    refetchInterval: (q) => (q.state.data?.some((f) => f.status === "pending" || f.status === "processing") ? 1500 : false),
  });
  const { data: config } = useQuery({ queryKey: ["config"], queryFn: filesApi.config, staleTime: Infinity });
  const [inspecting, setInspecting] = useState<ProjectFile | null>(null);
  const [devPanel, setDevPanel] = useState<"es" | "playground" | null>(null);
  const { data: rules, isError: rulesError } = useQuery({ queryKey: ["upload-rules"], queryFn: filesApi.rules, staleTime: Infinity });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["projects", projectId] });
    qc.invalidateQueries({ queryKey: ["projects"] });
  };

  const retry = useMutation({
    mutationFn: (f: ProjectFile) => filesApi.retry(f.id),
    onSuccess: refresh,
    onError: (e: Error) => setMessage({ title: "Retry failed", body: e.message }),
  });

  const stage = (items: Selected[], prunedDirs: string[] = []) => {
    if (!rules) return;
    const result = filterSelection(items, rules);
    result.skipped.push(...prunedDirs.map((path) => ({ path, reason: "ignored" })));
    if (result.accepted.length === 0) {
      setMessage({ title: "Nothing to upload", body: "None of the selected files are supported, or they were all in ignored folders (such as node_modules)." });
    } else setStaged(result);
  };

  const upload = useMutation({
    mutationFn: async (s: FilterResult) => {
      const report = await filesApi.upload(projectId, s.accepted);
      const serverSkipped = report.results.filter((r) => r.status === "rejected").map((r) => ({ path: r.path, reason: r.reason ?? "" }));
      return { report, reasons: [...s.skipped, ...serverSkipped] };
    },
    onSuccess: (r) => {
      const reasons = [...new Set(r.reasons.map((x) => REASON_LABEL[x.reason] ?? x.reason))];
      setStaged(null);
      const flagged = r.report.results
        .filter((x) => x.reason === "possible_secret")
        .map((x) => ({ path: x.path, detail: x.detail ?? null }));
      setMessage({ title: "Upload complete", report: r.report, skipped: { count: r.reasons.length, reasons }, flagged });
      refresh();
    },
    onError: (e: Error) => { setStaged(null); setMessage({ title: "Upload failed", body: e.message }); },
  });

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (!isAdmin || !rules || upload.isPending || processing) return;
    setScanning(true);
    try {
      const { selected, prunedDirs } = await readDropped(e.dataTransfer.items, rules);
      stage(selected, prunedDirs);
    } finally {
      setScanning(false);
    }
  };

  // The browser enumerates the whole folder after the OS picker closes and before `change` fires.
  // The window regains focus at that moment, so show the scanning window then; `change`/`cancel` end it.
  const openPicker = () => {
    const el = input.current;
    if (!el) return;
    const onFocus = () => setScanning(true);
    const onCancel = () => setScanning(false);
    window.addEventListener("focus", onFocus, { once: true });
    el.addEventListener("cancel", onCancel, { once: true });
    el.addEventListener("change", () => window.removeEventListener("focus", onFocus), { once: true });
    el.click();
  };

  const replaceInput = useRef<HTMLInputElement>(null);
  const [replaceTarget, setReplaceTarget] = useState<ProjectFile | null>(null);
  const replaceFor = (f: ProjectFile) => { setReplaceTarget(f); replaceInput.current?.click(); };
  const replace = useMutation({
    mutationFn: ({ f, file }: { f: ProjectFile; file: File }) => filesApi.replace(f.id, file),
    onSuccess: (r, { f }) => {
      setMessage({
        title: "File replaced",
        body: r.changed ? `${f.file_path} was replaced and queued for processing.` : `${f.file_path} is identical to the stored version, so nothing was reprocessed.`,
      });
      refresh();
    },
    onError: (e: Error) => setMessage({ title: "Replace failed", body: e.message }),
  });

  const remove = useMutation({
    mutationFn: (f: ProjectFile) => filesApi.remove(f.id),
    onSuccess: () => { setDeleting(null); refresh(); },
  });

  const actions: RowActions = {
    onOpen: setViewing,
    onDelete: setDeleting,
    onReplace: replaceFor,
    onRetry: (f) => retry.mutate(f),
    onChunks: config?.dev_mode && isAdmin ? setInspecting : undefined,
    locked: processing,
    readOnly: !isAdmin,
  };
  const matches = searchFiles(files, query);
  const tree = buildTree(files);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative min-w-[16rem] flex-1">
          <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search files by name or path…"
            aria-label="Search files"
            className="input pl-9"
          />
        </label>
        {config?.dev_mode && isAdmin && (
          <>
            <button onClick={() => setDevPanel("es")} className="btn-secondary">
              <Icon name="database" /> ES Inspector
            </button>
            <button onClick={() => setDevPanel("playground")} className="btn-secondary">
              <Icon name="bug" /> Playground
            </button>
          </>
        )}
        {isAdmin && <button
          onClick={openPicker}
          disabled={upload.isPending || !rules || processing}
          title={processing ? "Available when processing finishes" : undefined}
          className="btn-primary"
        >
          {upload.isPending || processing ? <Spinner /> : <Icon name="upload" />}
          {upload.isPending ? "Uploading…" : processing ? "Processing…" : files.length ? "Re-upload Project" : "Upload Project"}
        </button>}
        <input
          ref={input}
          type="file"
          multiple
          hidden
          data-testid="folder-input"
          {...{ webkitdirectory: "" }}
          onChange={(e) => {
            const selected = Array.from(e.target.files ?? []);
            e.target.value = "";
            setScanning(false);
            if (selected.length) stage(fromInput(selected));
          }}
        />
      </div>
      <input
        ref={replaceInput}
        type="file"
        hidden
        data-testid="replace-input"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file && replaceTarget) replace.mutate({ f: replaceTarget, file });
        }}
      />
      {rulesError && (
        <p role="alert" className="mt-3 text-sm text-red-600">
          Couldn't load the upload rules from the backend. Restart the backend (it may be an older version) and reload this page.
        </p>
      )}

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`card relative mt-4 p-2 transition ${dragging ? "border-brand-500 ring-4 ring-brand-500/15" : ""} ${!isLoading && files.length === 0 ? "border-dashed" : ""}`}
      >
        {dragging && (
          <div className="pointer-events-none absolute inset-0 z-10 flex animate-fade-in flex-col items-center justify-center gap-2 rounded-xl bg-brand-500/[0.07] text-brand-link backdrop-blur-[1px]">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-500/15"><Icon name="upload" className="h-6 w-6" /></span>
            <p className="text-sm font-semibold">Drop the project folder to upload it</p>
          </div>
        )}
        {isLoading && <div className="space-y-1.5 p-2">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-7" />)}</div>}
        {!isLoading && files.length === 0 && (
          isAdmin ? (
            <div className="flex flex-col items-center px-6 py-14 text-center">
              <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-500/10 text-brand-link ring-8 ring-brand-500/5">
                <Icon name="upload" className="h-6 w-6" />
              </span>
              <p className="mt-4 text-base font-semibold text-slate-900">Drag your project folder here</p>
              <p className="mt-1 max-w-sm text-sm text-slate-500">or click <span className="font-medium text-slate-700">Upload Project</span>. Ignored folders such as node_modules are skipped automatically.</p>
            </div>
          ) : (
            <p className="flex items-center gap-2 p-4 text-sm text-slate-500"><Icon name="lock" /> No files are shared with you in this project yet.</p>
          )
        )}
        {matches ? (
          matches.length === 0 ? (
            <p className="flex items-center gap-2 p-4 text-sm text-slate-500"><Icon name="search" /> No files match "{query.trim()}".</p>
          ) : (
            matches.map((f) => (
              <FileRow key={f.id} file={f} label={f.file_name} sublabel={f.file_path} actions={actions} />
            ))
          )
        ) : (
          tree.children.map((c) =>
            c.file ? (
              <FileRow key={c.path} file={c.file} label={c.name} actions={actions} />
            ) : (
              <TreeFolder key={c.path} node={c} depth={0} actions={actions} />
            ),
          )
        )}
      </div>

      {scanning && createPortal(
        <div className="fixed inset-0 z-50 flex animate-fade-in items-center justify-center bg-slate-900/40 p-4 backdrop-blur-[2px]">
          <div
            role="status"
            aria-live="polite"
            className="flex w-60 animate-scale-in flex-col items-center gap-3 rounded-2xl border border-slate-200 bg-surface px-6 py-6 shadow-pop"
          >
            <span
              aria-hidden="true"
              className="h-8 w-8 animate-spin rounded-full border-[3px] border-slate-200 border-t-brand-600"
            />
            <p className="text-sm font-medium text-slate-700">Scanning…</p>
          </div>
        </div>,
        document.body,
      )}

      {staged && (
        <Modal title={files.length ? "Re-upload project files?" : "Upload project files?"} onClose={() => !upload.isPending && setStaged(null)}>
          <p className="text-sm text-slate-600">
            <span className="font-medium">{staged.accepted.length}</span> file{staged.accepted.length === 1 ? "" : "s"} (
            {(staged.accepted.reduce((n, a) => n + a.file.size, 0) / 1024 / 1024).toFixed(1)} MB) will be uploaded.
            {staged.skipped.length > 0 && <> {staged.skipped.length} other item{staged.skipped.length === 1 ? " was" : "s were"} skipped (ignored folders, unsupported types or size limits).</>}
          </p>
          <p className="mt-3 flex gap-2.5 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs leading-5 text-slate-600">
            <Icon name="lock" className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
            <span>
              <span className="font-medium text-slate-700">Privacy:</span> file contents leave this machine. Text is sent to Google
              (Gemini) to create search embeddings, and the passages relevant to a question are sent to the chat
              provider (Groq or Gemini) to write the answer. Files that look like they contain credentials are skipped.
            </span>
          </p>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setStaged(null)} disabled={upload.isPending} className="btn-secondary">Cancel</button>
            <button onClick={() => upload.mutate(staged)} disabled={upload.isPending} className="btn-primary">
              {upload.isPending ? <Spinner /> : <Icon name="upload" />} {upload.isPending ? "Uploading…" : "Upload"}
            </button>
          </div>
        </Modal>
      )}
      {message && (
        <Modal title={message.title} onClose={() => setMessage(null)}>
          {message.body && <p className="text-sm text-slate-600">{message.body}</p>}
          {message.report && <UploadReportView report={message.report} skipped={message.skipped ?? { count: 0, reasons: [] }} />}
          {message.flagged && message.flagged.length > 0 && (
            <div role="alert" className="mt-3 rounded-lg border border-amber-500/30 bg-amber-50 p-3 text-sm text-amber-700">
              <p className="font-medium">
                {message.flagged.length} file{message.flagged.length === 1 ? " was" : "s were"} skipped because {message.flagged.length === 1 ? "it looks" : "they look"} like {message.flagged.length === 1 ? "a credential" : "credentials"}:
              </p>
              <ul className="mt-1 max-h-32 overflow-auto font-mono text-xs">
                {message.flagged.map((f) => <li key={f.path} className="truncate" title={f.path}>{f.path}{f.detail ? ` (${f.detail})` : ""}</li>)}
              </ul>
              <p className="mt-1 text-xs">Remove the secret from the file (or load it from an environment variable) and upload again.</p>
            </div>
          )}
          <div className="mt-5 flex justify-end">
            <button onClick={() => setMessage(null)} className="btn-primary px-5">OK</button>
          </div>
        </Modal>
      )}
      {devPanel === "es" && <ESInspector projectId={projectId} onClose={() => setDevPanel(null)} />}
      {devPanel === "playground" && <RetrievalPlayground projectId={projectId} onClose={() => setDevPanel(null)} />}
      {inspecting && <ChunkInspector projectId={projectId} file={inspecting} onClose={() => setInspecting(null)} />}
      {viewing && (
        <Suspense fallback={null}>
          <FloatingFileViewer file={viewing} onClose={() => setViewing(null)} onAsk={onAsk} />
        </Suspense>
      )}
      {deleting && (
        <Modal title="Delete file?" onClose={() => setDeleting(null)}>
          <p className="text-sm text-slate-600">
            <span className="font-medium">{deleting.file_path}</span> will be removed from this project. This can't be undone.
          </p>
          {remove.isError && <p className="mt-2 text-sm text-red-600">Couldn't delete the file. Please try again.</p>}
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setDeleting(null)} className="btn-secondary">Cancel</button>
            <button
              onClick={() => remove.mutate(deleting)}
              disabled={remove.isPending}
              className="btn bg-danger text-white hover:bg-danger-hover"
            >
              Delete
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
