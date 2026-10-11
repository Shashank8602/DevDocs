import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { filesApi, ProjectFile, UploadReport } from "../../api/files";
import { readDropped } from "../../lib/dropFolder";
import { filterSelection, FilterResult, fromInput, REASON_LABEL, Selected } from "../../lib/ignore";
import { buildTree, searchFiles, TreeNode } from "../../lib/tree";
import Modal from "../Modal";
import UploadReportView from "./UploadReportView";
import ChunkInspector from "./ChunkInspector";
import ESInspector from "./ESInspector";
import RetrievalPlayground from "./RetrievalPlayground";
import FloatingFileViewer from "../viewer/FloatingFileViewer";

const STATUS_LABEL: Record<string, string> = { pending: "Pending", processing: "Processing", ready: "Ready", failed: "Failed" };

interface RowActions {
  onOpen: (f: ProjectFile) => void;
  onDelete: (f: ProjectFile) => void;
  onReplace: (f: ProjectFile) => void;
  onRetry: (f: ProjectFile) => void;
  onChunks?: (f: ProjectFile) => void; // dev mode only
  locked: boolean; // the project is ingesting: replacing is not allowed
}

function StatusBadge({ file, actions }: { file: ProjectFile; actions: RowActions }) {
  const [open, setOpen] = useState(false);
  const base = "ml-2 shrink-0 rounded-full px-2 py-0.5 text-xs";
  if (file.status !== "failed") {
    return (
      <span className={`${base} bg-slate-100 text-slate-600 ${file.status === "processing" ? "animate-pulse" : ""}`}>
        {STATUS_LABEL[file.status] ?? file.status}
      </span>
    );
  }
  return (
    <span className="relative">
      <button onClick={() => setOpen(!open)} aria-expanded={open} className={`${base} bg-red-100 text-red-700 hover:bg-red-200`}>
        Failed
      </button>
      {open && (
        <div role="dialog" aria-label="Processing failed" className="absolute right-0 top-full z-30 mt-1 w-64 rounded-md border border-slate-200 bg-surface p-3 shadow-lg">
          <p className="text-sm text-slate-700">DevDocs couldn't process this file.</p>
          <div className="mt-3 flex justify-end gap-2">
            <button
              onClick={() => { setOpen(false); actions.onRetry(file); }}
              className="rounded-md border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100"
            >
              Retry
            </button>
            <button
              onClick={() => { setOpen(false); actions.onReplace(file); }}
              disabled={actions.locked}
              className="rounded-md bg-brand-600 px-3 py-1 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50"
            >
              Replace File
            </button>
          </div>
        </div>
      )}
    </span>
  );
}

function FileRow({ file, label, actions }: { file: ProjectFile; label: string; actions: RowActions }) {
  const hover = "ml-2 rounded px-1.5 text-xs text-slate-400 opacity-0 focus:opacity-100 group-hover:opacity-100";
  return (
    <div className="group flex items-center rounded px-2 py-1 hover:bg-slate-100">
      <button onClick={() => actions.onOpen(file)} className="min-w-0 flex-1 truncate text-left text-sm text-slate-800" title={file.file_path}>
        {label}
      </button>
      <StatusBadge file={file} actions={actions} />
      {actions.onChunks && (
        <button onClick={() => actions.onChunks!(file)} className={`${hover} hover:text-brand-link`}>
          Chunks
        </button>
      )}
      <button onClick={() => actions.onReplace(file)} disabled={actions.locked} className={`${hover} hover:text-brand-link disabled:cursor-not-allowed`}>
        Replace
      </button>
      <button
        onClick={() => actions.onDelete(file)}
        aria-label={`Delete ${file.file_name}`}
        className="ml-2 rounded px-1.5 text-sm text-slate-400 opacity-0 hover:text-red-600 focus:opacity-100 group-hover:opacity-100"
      >
        ✕
      </button>
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
        className="flex w-full items-center gap-1 rounded px-2 py-1 text-left text-sm font-medium text-slate-700 hover:bg-slate-100"
      >
        <span className="w-3 text-xs text-slate-400">{open ? "▾" : "▸"}</span>
        {node.name}
      </button>
      {open && (
        <div className="ml-4 border-l border-slate-200 pl-1">
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

export default function FilesTab({ projectId, processing }: { projectId: string; processing: boolean }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [viewing, setViewing] = useState<ProjectFile | null>(null);
  const [deleting, setDeleting] = useState<ProjectFile | null>(null);
  const [staged, setStaged] = useState<FilterResult | null>(null);
  const [message, setMessage] = useState<{ title: string; body?: string; report?: UploadReport; skipped?: { count: number; reasons: string[] } } | null>(null);
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
      setMessage({ title: "Upload complete", report: r.report, skipped: { count: r.reasons.length, reasons } });
      refresh();
    },
    onError: (e: Error) => { setStaged(null); setMessage({ title: "Upload failed", body: e.message }); },
  });

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (!rules || upload.isPending || processing) return;
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
    onChunks: config?.dev_mode ? setInspecting : undefined,
    locked: processing,
  };
  const matches = searchFiles(files, query);
  const tree = buildTree(files);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search files by name or path…"
          aria-label="Search files"
          className="min-w-[16rem] flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        <button
          onClick={openPicker}
          disabled={upload.isPending || !rules || processing}
          title={processing ? "Available when processing finishes" : undefined}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50"
        >
          {upload.isPending ? "Uploading…" : processing ? "Processing…" : files.length ? "Re-upload Project" : "Upload Project"}
        </button>
        {config?.dev_mode && (
          <>
            <button onClick={() => setDevPanel("es")} className="rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100">
              ES Inspector
            </button>
            <button onClick={() => setDevPanel("playground")} className="rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100">
              Playground
            </button>
          </>
        )}
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
        className={`mt-4 rounded-lg border bg-surface p-2 ${dragging ? "border-brand-500 ring-2 ring-brand-500/30" : "border-slate-200"}`}
      >
        {dragging && <p className="p-3 text-sm font-medium text-brand-link">Drop the project folder to upload it</p>}
        {isLoading && <p className="p-3 text-sm text-slate-500">Loading…</p>}
        {!isLoading && files.length === 0 && <p className="p-3 text-sm text-slate-500">No files yet. Click Upload Project, or drag your project folder here.</p>}
        {matches ? (
          matches.length === 0 ? (
            <p className="p-3 text-sm text-slate-500">No files match "{query.trim()}".</p>
          ) : (
            matches.map((f) => (
              <FileRow key={f.id} file={f} label={f.file_path} actions={actions} />
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

      {scanning && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
          <div
            role="status"
            aria-live="polite"
            className="flex w-56 flex-col items-center gap-3 rounded-lg bg-surface px-6 py-5 shadow-xl"
          >
            <p className="text-sm font-medium text-slate-700">Scanning…</p>
            <span
              aria-hidden="true"
              className="h-7 w-7 animate-spin rounded-full border-[3px] border-slate-200 border-t-brand-600"
            />
          </div>
        </div>
      )}

      {staged && (
        <Modal title={files.length ? "Re-upload project files?" : "Upload project files?"} onClose={() => !upload.isPending && setStaged(null)}>
          <p className="text-sm text-slate-600">
            <span className="font-medium">{staged.accepted.length}</span> file{staged.accepted.length === 1 ? "" : "s"} (
            {(staged.accepted.reduce((n, a) => n + a.file.size, 0) / 1024 / 1024).toFixed(1)} MB) will be uploaded.
            {staged.skipped.length > 0 && <> {staged.skipped.length} other item{staged.skipped.length === 1 ? " was" : "s were"} skipped (ignored folders, unsupported types or size limits).</>}
          </p>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setStaged(null)} disabled={upload.isPending} className="rounded-md border border-slate-300 px-4 py-2 text-sm disabled:opacity-50">Cancel</button>
            <button
              onClick={() => upload.mutate(staged)}
              disabled={upload.isPending}
              className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50"
            >
              {upload.isPending ? "Uploading…" : "Upload"}
            </button>
          </div>
        </Modal>
      )}
      {message && (
        <Modal title={message.title} onClose={() => setMessage(null)}>
          {message.body && <p className="text-sm text-slate-600">{message.body}</p>}
          {message.report && <UploadReportView report={message.report} skipped={message.skipped ?? { count: 0, reasons: [] }} />}
          <div className="mt-5 flex justify-end">
            <button onClick={() => setMessage(null)} className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500">OK</button>
          </div>
        </Modal>
      )}
      {devPanel === "es" && <ESInspector projectId={projectId} onClose={() => setDevPanel(null)} />}
      {devPanel === "playground" && <RetrievalPlayground projectId={projectId} onClose={() => setDevPanel(null)} />}
      {inspecting && <ChunkInspector projectId={projectId} file={inspecting} onClose={() => setInspecting(null)} />}
      {viewing && <FloatingFileViewer file={viewing} onClose={() => setViewing(null)} />}
      {deleting && (
        <Modal title="Delete file?" onClose={() => setDeleting(null)}>
          <p className="text-sm text-slate-600">
            <span className="font-medium">{deleting.file_path}</span> will be removed from this project. This can't be undone.
          </p>
          {remove.isError && <p className="mt-2 text-sm text-red-600">Couldn't delete the file. Please try again.</p>}
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setDeleting(null)} className="rounded-md border border-slate-300 px-4 py-2 text-sm">Cancel</button>
            <button
              onClick={() => remove.mutate(deleting)}
              disabled={remove.isPending}
              className="rounded-md bg-danger px-4 py-2 text-sm font-medium text-white hover:bg-danger-hover disabled:opacity-50"
            >
              Delete
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
