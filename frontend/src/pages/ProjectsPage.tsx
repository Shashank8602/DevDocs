import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Project, projectsApi } from "../api/projects";
import { DeleteProjectDialog, ProjectFormDialog } from "../components/ProjectDialogs";
import { Badge, EmptyState, Icon, Skeleton } from "../components/ui";

type Dialog = { kind: "create" } | { kind: "rename"; project: Project } | { kind: "delete"; project: Project } | null;

const TONE = { ready: "success", processing: "brand", failed: "danger", needs_reindex: "warning", empty: "neutral" } as const;
const LABEL = {
  ready: "Ready", processing: "Processing", failed: "Failed", needs_reindex: "Re-index required", empty: "Empty",
} as const;

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export default function ProjectsPage() {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Dialog>(null);
  const close = () => setDialog(null);

  const { data: projects, isLoading, isError } = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list });

  const onDone = () => {
    qc.invalidateQueries({ queryKey: ["projects"] });
    close();
  };
  const create = useMutation({ mutationFn: projectsApi.create, onSuccess: onDone });
  const rename = useMutation({
    mutationFn: ({ id, ...body }: { id: string; name: string; description: string }) => projectsApi.update(id, body),
    onSuccess: onDone,
  });
  const remove = useMutation({ mutationFn: projectsApi.remove, onSuccess: onDone });

  const openDialog = (d: Dialog) => {
    create.reset(); rename.reset(); remove.reset();
    setDialog(d);
  };

  return (
    <section>
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
          <p className="mt-1 text-sm text-slate-500">Each project is its own searchable knowledge base.</p>
        </div>
        <button onClick={() => openDialog({ kind: "create" })} className="btn-primary">
          <Icon name="plus" /> New Project
        </button>
      </div>

      {isLoading && (
        <ul className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => <li key={i}><Skeleton className="h-40" /></li>)}
        </ul>
      )}
      {isError && <p className="mt-8 text-red-600">Couldn't load projects. Is the backend running?</p>}

      {projects && projects.length === 0 && (
        <div className="mt-8">
          <EmptyState
            icon="folder"
            title="No projects yet"
            action={
              <button onClick={() => openDialog({ kind: "create" })} className="btn-primary">
                <Icon name="plus" /> Create your first project
              </button>
            }
          >
            Create a project, upload a folder, and chat with your code and docs.
          </EmptyState>
        </div>
      )}

      {projects && projects.length > 0 && (
        <ul className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p, i) => (
            <li
              key={p.id}
              style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}
              className="card group relative flex animate-slide-up flex-col p-5 transition duration-200 hover:-translate-y-0.5 hover:shadow-lift"
            >
              <div className="flex items-start justify-between gap-3">
                <Link
                  to={`/projects/${p.id}`}
                  className="min-w-0 text-lg font-semibold tracking-tight after:absolute after:inset-0 after:rounded-xl hover:text-brand-link"
                >
                  <span className="block truncate">{p.name}</span>
                </Link>
                <Badge tone={TONE[p.status] ?? "neutral"} dot>{LABEL[p.status] ?? p.status}</Badge>
              </div>
              <p className="mt-1 line-clamp-2 min-h-[2.5rem] text-sm text-slate-500">{p.description || "No description"}</p>
              <p className="mt-3 text-xs text-slate-500">
                {p.file_count} {p.file_count === 1 ? "file" : "files"} · Updated {formatDate(p.updated_at)}
              </p>
              <div className="relative z-10 mt-4 flex gap-2 text-sm">
                <button onClick={() => openDialog({ kind: "rename", project: p })} className="btn-secondary px-3 py-1.5">Edit</button>
                <button onClick={() => openDialog({ kind: "delete", project: p })} className="btn-danger px-3 py-1.5">Delete</button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {dialog?.kind === "create" && (
        <ProjectFormDialog
          title="New project"
          submitLabel="Create"
          busy={create.isPending}
          error={create.error?.message}
          onClose={close}
          onSubmit={({ name, description }) => create.mutate({ name, description: description || undefined })}
        />
      )}
      {dialog?.kind === "rename" && (
        <ProjectFormDialog
          title="Edit project"
          submitLabel="Save"
          initialName={dialog.project.name}
          initialDescription={dialog.project.description ?? ""}
          busy={rename.isPending}
          error={rename.error?.message}
          onClose={close}
          onSubmit={(values) => rename.mutate({ id: dialog.project.id, ...values })}
        />
      )}
      {dialog?.kind === "delete" && (
        <DeleteProjectDialog
          name={dialog.project.name}
          busy={remove.isPending}
          error={remove.error?.message}
          onClose={close}
          onConfirm={() => remove.mutate(dialog.project.id)}
        />
      )}
    </section>
  );
}
