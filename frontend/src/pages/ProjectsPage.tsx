import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Project, projectsApi } from "../api/projects";
import { useAuth } from "../auth/AuthContext";
import { DeleteProjectDialog, ProjectFormDialog } from "../components/ProjectDialogs";
import { Badge, EmptyState, Icon, Menu, Monogram, Skeleton } from "../components/ui";

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
  const { isAdmin } = useAuth();
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
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2.5 text-2xl font-semibold tracking-tight">
            Projects
            {projects && projects.length > 0 && (
              <span className="rounded-full bg-slate-200/70 px-2 py-0.5 text-xs font-medium tabular-nums text-slate-600">{projects.length}</span>
            )}
          </h1>
          <p className="mt-1 text-sm text-slate-500">Each project is its own searchable knowledge base.</p>
        </div>
        {isAdmin && (
          <button onClick={() => openDialog({ kind: "create" })} className="btn-primary">
            <Icon name="plus" /> New Project
          </button>
        )}
      </div>

      {isLoading && (
        <ul className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => <li key={i}><Skeleton className="h-44 rounded-xl" /></li>)}
        </ul>
      )}
      {isError && <p className="mt-8 text-red-600">Couldn't load projects. Is the backend running?</p>}

      {projects && projects.length === 0 && (
        <div className="mt-8">
          <EmptyState
            icon="folder"
            title="No projects yet"
            action={isAdmin ? (
              <button onClick={() => openDialog({ kind: "create" })} className="btn-primary">
                <Icon name="plus" /> Create your first project
              </button>
            ) : undefined}
          >
            {isAdmin
              ? "Create a project, upload a folder, and chat with your code and docs."
              : "Nothing has been shared with you yet. Ask an admin to give you access to a project."}
          </EmptyState>
        </div>
      )}

      {projects && projects.length > 0 && (
        <ul className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p, i) => (
            <li
              key={p.id}
              style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}
              className="card group relative flex animate-slide-up flex-col p-5 transition focus-within:z-20 duration-200 hover:-translate-y-0.5 hover:border-brand-500/40 hover:shadow-lift"
            >
              <div className="flex items-start gap-3.5">
                <Monogram name={p.name} className="h-11 w-11 rounded-xl text-base" />
                <div className="min-w-0 flex-1 pt-0.5">
                  <Link
                    to={`/projects/${p.id}`}
                    className="block min-w-0 text-base font-semibold tracking-tight text-slate-900 after:absolute after:inset-0 after:rounded-xl group-hover:text-brand-link"
                  >
                    <span className="block truncate">{p.name}</span>
                  </Link>
                  <div className="mt-1"><Badge tone={TONE[p.status] ?? "neutral"} dot>{LABEL[p.status] ?? p.status}</Badge></div>
                </div>
                {isAdmin && (
                  <div className="relative z-10 -mr-1.5 -mt-1">
                    <Menu label={`Actions for ${p.name}`} button={<Icon name="more" />}>
                      {(closeMenu) => (
                        <>
                          <button role="menuitem" className="menu-item" onClick={() => { closeMenu(); openDialog({ kind: "rename", project: p }); }}>
                            <Icon name="settings" className="h-4 w-4 text-slate-500" /> Edit details
                          </button>
                          <button role="menuitem" className="menu-item text-red-600 hover:bg-red-50 hover:text-red-700" onClick={() => { closeMenu(); openDialog({ kind: "delete", project: p }); }}>
                            <Icon name="trash" className="h-4 w-4" /> Delete project
                          </button>
                        </>
                      )}
                    </Menu>
                  </div>
                )}
              </div>
              <p className="mt-3 line-clamp-2 min-h-[2.5rem] text-sm leading-5 text-slate-500">{p.description || "No description"}</p>
              {p.paused_until && (
                <p className="mt-2 flex items-center gap-1.5 text-xs text-amber-700">
                  <Icon name="clock" className="h-3.5 w-3.5 shrink-0" /> Waiting for the embedding quota. Nothing failed; it resumes by itself.
                </p>
              )}
              <div className="mt-4 flex items-center gap-4 border-t border-slate-200/80 pt-3 text-xs text-slate-500">
                <span className="flex items-center gap-1.5"><Icon name="file" className="h-3.5 w-3.5" />{p.file_count} {p.file_count === 1 ? "file" : "files"}</span>
                <span className="flex items-center gap-1.5"><Icon name="clock" className="h-3.5 w-3.5" />Updated {formatDate(p.updated_at)}</span>
                <Icon name="arrow-right" className="ml-auto h-4 w-4 -translate-x-1 text-brand-link opacity-0 transition group-hover:translate-x-0 group-hover:opacity-100" />
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
