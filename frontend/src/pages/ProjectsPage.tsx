import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Project, projectsApi } from "../api/projects";
import { DeleteProjectDialog, ProjectFormDialog } from "../components/ProjectDialogs";

type Dialog = { kind: "create" } | { kind: "rename"; project: Project } | { kind: "delete"; project: Project } | null;

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
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Projects</h1>
        <button
          onClick={() => openDialog({ kind: "create" })}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500"
        >
          New Project
        </button>
      </div>

      {isLoading && <p className="mt-8 text-slate-500">Loading…</p>}
      {isError && <p className="mt-8 text-red-600">Couldn't load projects. Is the backend running?</p>}

      {projects && projects.length === 0 && (
        <div className="mt-8 rounded-lg border border-dashed border-slate-300 bg-surface p-12 text-center text-slate-500">
          No projects yet.
        </div>
      )}

      {projects && projects.length > 0 && (
        <ul className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <li key={p.id} className="flex flex-col rounded-lg border border-slate-200 bg-surface p-5 shadow-sm">
              <Link to={`/projects/${p.id}`} className="text-lg font-semibold hover:text-brand-link">
                {p.name}
              </Link>
              {p.description && <p className="mt-1 line-clamp-2 text-sm text-slate-500">{p.description}</p>}
              <p className="mt-3 text-xs text-slate-400">
                {p.file_count} {p.file_count === 1 ? "file" : "files"} · Updated {formatDate(p.updated_at)}
              </p>
              <div className="mt-4 flex gap-2 text-sm">
                <button onClick={() => openDialog({ kind: "rename", project: p })} className="rounded-md border border-slate-300 bg-surface px-3 py-1.5 font-medium text-slate-700 shadow-sm transition hover:bg-slate-100 active:scale-95">
                  Edit
                </button>
                <button onClick={() => openDialog({ kind: "delete", project: p })} className="rounded-md border border-red-200 bg-red-50 px-3 py-1.5 font-medium text-red-600 shadow-sm transition hover:bg-red-100 active:scale-95">
                  Delete
                </button>
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
