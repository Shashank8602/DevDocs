import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { accessApi, AccessRule, AuditRow, Member as Person, Visibility } from "../../api/access";
import { useAuth } from "../../auth/AuthContext";
import {
  buildTree, coverage, findNode, governingRule, readers, ruleFor, TreeNode,
} from "../../lib/accessTree";
import { Badge, EmptyState, Icon, Monogram, Skeleton, Spinner, useToast } from "../ui";

const VIEWS = ["People", "By folder", "By person", "Matrix", "Audit log"] as const;
type View = (typeof VIEWS)[number];

function useAccess(projectId: string) {
  const access = useQuery({ queryKey: ["access", projectId], queryFn: () => accessApi.get(projectId) });
  const people = useQuery({ queryKey: ["access", projectId, "members"], queryFn: () => accessApi.members(projectId) });
  const members = useMemo(() => people.data ?? [], [people.data]);
  const tree = useMemo(() => buildTree(access.data?.files ?? []), [access.data]);
  return { access, people, members, tree, rules: access.data?.rules ?? [] };
}

function useSaveRule(projectId: string) {
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["access", projectId] });
    qc.invalidateQueries({ queryKey: ["files"] });
  };
  const save = useMutation({
    mutationFn: (rule: AccessRule) => accessApi.put(projectId, rule),
    onSuccess: refresh,
    onError: (e: Error) => toast(e.message, "error"),
  });
  const remove = useMutation({
    mutationFn: (path: string) => accessApi.remove(projectId, path),
    onSuccess: refresh,
    onError: (e: Error) => toast(e.message, "error"),
  });
  return { save, remove };
}

/* ---------- People of this project ---------- */
function People({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { people, members } = useAccess(projectId);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["access", projectId] });
    qc.invalidateQueries({ queryKey: ["projects"] });
  };
  const add = useMutation({
    mutationFn: () => accessApi.addMember(projectId, { email: email.trim(), name: name.trim(), password: password || undefined }),
    onSuccess: (m) => {
      toast(`${m.email} added to this project. Share folders with them on the By folder view.`, "success");
      setEmail(""); setName(""); setPassword(""); setError(null);
      refresh();
    },
    onError: (e: Error) => setError(e.message),
  });
  const remove = useMutation({
    mutationFn: (id: string) => accessApi.removeMember(projectId, id),
    onSuccess: () => { toast("Removed from this project.", "success"); refresh(); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,24rem)_minmax(0,1fr)]">
      <form
        className="card h-fit space-y-3 p-5"
        onSubmit={(e) => { e.preventDefault(); setError(null); add.mutate(); }}
        aria-label="Add a person to this project"
      >
        <h3 className="text-base font-semibold">Add a person to this project</h3>
        <p className="text-sm text-slate-500">
          They can open only this project, and only the files you share with them. Enter an existing person's email, or
          fill in a password to create a new account.
        </p>
        <label className="block">
          <span className="label">Email</span>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="input" autoComplete="off" />
        </label>
        <label className="block">
          <span className="label">Name <span className="font-normal text-slate-400">(new people only)</span></span>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} className="input" autoComplete="off" />
        </label>
        <label className="block">
          <span className="label">Password <span className="font-normal text-slate-400">(new people only, 8+ characters)</span></span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className="input" autoComplete="new-password" />
        </label>
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        <button type="submit" disabled={add.isPending || !email.trim()} className="btn-primary w-full">
          {add.isPending && <Spinner />} <Icon name="plus" /> Add to project
        </button>
      </form>
      <div>
        {people.isLoading && <Skeleton className="h-32" />}
        {!people.isLoading && members.length === 0 && (
          <EmptyState icon="users" title="Nobody in this project yet">Add people on the left. Admins always see every project.</EmptyState>
        )}
        {members.length > 0 && (
          <ul className="card divide-y divide-slate-100">
            {members.map((m) => (
              <li key={m.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="flex min-w-0 items-center gap-3">
                  <Monogram name={m.name || m.email} className="h-8 w-8 rounded-full text-xs" />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{m.name || m.email}</p>
                    {m.name && <p className="truncate text-xs text-slate-500">{m.email}</p>}
                  </div>
                </div>
                <button
                  disabled={remove.isPending}
                  onClick={() => window.confirm(`Remove ${m.email} from this project? Their chats here are deleted.`) && remove.mutate(m.id)}
                  className="btn-danger px-3 py-1.5"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/* ---------- By folder ---------- */
function TreeRow({
  node, depth, selected, onSelect, rules,
}: { node: TreeNode; depth: number; selected: string; onSelect: (p: string) => void; rules: AccessRule[] }) {
  const [open, setOpen] = useState(depth === 0);
  const own = rules.find((r) => r.path === node.path);
  const rule = governingRule(rules, node.path);
  const label = rule ? (rule.visibility === "everyone" ? "Everyone" : rule.visibility === "people" ? `${rule.user_ids.length} people` : "Admins only") : "Admins only";
  return (
    <li>
      <div
        className={`flex items-center gap-1 rounded-md border pr-2 ${
          selected === node.path ? "border-brand-500/30 bg-brand-500/10" : "border-transparent hover:bg-slate-100"
        }`}
        style={{ paddingLeft: depth * 14 + 4 }}
      >
        {node.children.length > 0 ? (
          <button
            type="button" aria-label={open ? "Collapse" : "Expand"} aria-expanded={open} onClick={() => setOpen(!open)}
            className="rounded p-1 text-slate-500 hover:bg-slate-200/70"
          >
            <Icon name="chevron-right" className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-90" : ""}`} />
          </button>
        ) : <span className="w-6" />}
        <button
          type="button" onClick={() => onSelect(node.path)} aria-pressed={selected === node.path}
          className="flex min-w-0 flex-1 items-center gap-2 py-1.5 text-left text-sm"
        >
          <Icon name={node.isFile ? "file" : "folder"} className="h-4 w-4 shrink-0 text-slate-400" />
          <span className="truncate">{node.name}</span>
          {own && <span className="shrink-0 rounded-md bg-brand-500/10 px-1.5 text-xs font-medium text-brand-link">rule</span>}
        </button>
        <span className="shrink-0 text-xs text-slate-500">{label}</span>
      </div>
      {open && node.children.length > 0 && (
        <ul>
          {node.children.map((c) => (
            <TreeRow key={c.path} node={c} depth={depth + 1} selected={selected} onSelect={onSelect} rules={rules} />
          ))}
        </ul>
      )}
    </li>
  );
}

function SharePanel({
  projectId, node, rules, members,
}: { projectId: string; node: TreeNode; rules: AccessRule[]; members: Person[] }) {
  const { save, remove } = useSaveRule(projectId);
  const own = rules.find((r) => r.path === node.path) ?? null;
  const inherited = governingRule(rules, node.path);
  const current: Visibility = inherited?.visibility ?? "admin";
  const chosen = new Set(inherited?.visibility === "people" ? inherited.user_ids : []);
  const busy = save.isPending || remove.isPending;

  const setVisibility = (visibility: Visibility) =>
    save.mutate({ path: node.path, visibility, user_ids: visibility === "people" ? [...chosen] : [] });
  const toggle = (id: string) => {
    const next = new Set(chosen);
    if (next.has(id)) next.delete(id); else next.add(id);
    save.mutate({ path: node.path, visibility: "people", user_ids: [...next] });
  };

  const where = node.path === "" ? "the whole project" : node.path;
  return (
    <div className="card p-5">
      <h3 className="truncate text-base font-semibold" title={where}>{node.path === "" ? "Whole project" : node.path}</h3>
      <p className="mt-1 text-sm text-slate-500">
        {own
          ? "This path has its own rule."
          : inherited
            ? `Inherited from ${inherited.path === "" ? "the whole project" : inherited.path}.`
            : "No rule covers this, so only admins can read it."}{" "}
        {node.files.length} {node.files.length === 1 ? "file" : "files"}.
      </p>
      <fieldset className="mt-4 space-y-2" disabled={busy}>
        <legend className="sr-only">Who can read {where}</legend>
        {([
          ["everyone", "Everyone", "Every signed-in person"],
          ["people", "Specific people", "Only the people you pick"],
          ["admin", "Only admins", "Hidden from every member"],
        ] as const).map(([value, title, hint]) => (
          <label
            key={value}
            className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${
              current === value ? "border-brand-500/40 bg-brand-500/10" : "border-slate-200 hover:bg-slate-50"
            }`}
          >
            <input
              type="radio" name={`vis-${node.path}`} checked={current === value} onChange={() => setVisibility(value)}
              className="mt-1"
            />
            <span>
              <span className="block text-sm font-medium">{title}</span>
              <span className="block text-xs text-slate-500">{hint}</span>
            </span>
          </label>
        ))}
      </fieldset>

      {current === "people" && (
        <div className="mt-4">
          <p className="label">People who can read it</p>
          {members.length === 0 ? (
            <p className="text-sm text-slate-500">Nobody is in this project yet. Add people on the People view first.</p>
          ) : (
            <ul className="max-h-56 space-y-1 overflow-y-auto">
              {members.map((m) => (
                <li key={m.id}>
                  <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-slate-100">
                    <input type="checkbox" checked={chosen.has(m.id)} disabled={busy} onChange={() => toggle(m.id)} />
                    <span className="truncate">{m.name || m.email}</span>
                    {m.name && <span className="truncate text-xs text-slate-500">{m.email}</span>}
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="mt-4 flex items-center justify-between gap-2">
        <span className="text-xs text-slate-500">Changes apply to the next question. No re-index.</span>
        {own && (
          <button disabled={busy} onClick={() => remove.mutate(node.path)} className="btn-secondary px-3 py-1.5">
            {busy && <Spinner />} Remove this rule
          </button>
        )}
      </div>
    </div>
  );
}

function ByFolder({ projectId }: { projectId: string }) {
  const { access, members, tree, rules } = useAccess(projectId);
  const [selected, setSelected] = useState("");
  if (access.isLoading) return <Skeleton className="h-64" />;
  if (access.isError) return <p className="text-red-600">Could not load access rules.</p>;
  if (tree.files.length === 0) {
    return <EmptyState icon="folder" title="No files yet">Upload files first, then choose who can read each folder.</EmptyState>;
  }
  const node = findNode(tree, selected) ?? tree;
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
      <div className="card max-h-[32rem] overflow-y-auto p-2">
        <ul><TreeRow node={tree} depth={0} selected={selected} onSelect={setSelected} rules={rules} /></ul>
      </div>
      <SharePanel key={node.path} projectId={projectId} node={node} rules={rules} members={members} />
    </div>
  );
}

/* ---------- By person ---------- */
function ByPerson({ projectId }: { projectId: string }) {
  const { members } = useAccess(projectId);
  const { startPreview } = useAuth();
  const navigate = useNavigate();
  const [userId, setUserId] = useState("");
  const chosen = userId || members[0]?.id || "";
  const view = useQuery({
    queryKey: ["access", projectId, "person", chosen],
    queryFn: () => accessApi.person(projectId, chosen),
    enabled: !!chosen,
  });
  if (members.length === 0) {
    return <EmptyState icon="folder" title="No members yet">Add people on the People view, then see what each of them can read.</EmptyState>;
  }
  const readable = view.data?.filter((f) => f.can_read).length ?? 0;
  return (
    <div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="block min-w-[14rem]">
          <span className="label">Person</span>
          <select value={chosen} onChange={(e) => setUserId(e.target.value)} className="input select">
            {members.map((m) => <option key={m.id} value={m.id}>{m.name || m.email}</option>)}
          </select>
        </label>
        <button
          className="btn-secondary"
          onClick={() => { startPreview(chosen); navigate(`/projects/${projectId}?tab=files`); }}
        >
          <Icon name="eye" /> Preview app as this person
        </button>
        {view.data && <span className="pb-2 text-sm text-slate-500">Can read {readable} of {view.data.length} files.</span>}
      </div>
      {view.isLoading && <Skeleton className="mt-4 h-40" />}
      {view.data && (
        <ul className="card mt-4 max-h-[30rem] divide-y divide-slate-100 overflow-y-auto">
          {view.data.map((f) => (
            <li key={f.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
              <Badge tone={f.can_read ? "success" : "neutral"}>{f.can_read ? "Can read" : "Hidden"}</Badge>
              <span className="min-w-0 flex-1 truncate font-mono text-xs" title={f.path}>{f.path}</span>
              <span className="hidden max-w-[22rem] truncate text-xs text-slate-500 sm:block" title={f.reason}>{f.reason}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------- Matrix ---------- */
function MatrixRows({
  node, depth, rules, members, onToggle, busy,
}: {
  node: TreeNode; depth: number; rules: AccessRule[]; members: Person[];
  onToggle: (node: TreeNode, user: Person, state: "none" | "partial" | "all") => void; busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  // a folder's children are listed when it has few files or a file inside has its own rule
  const hasOwnRule = rules.some((r) => r.path !== node.path && r.path.startsWith(node.path ? node.path + "/" : ""));
  const expandable = node.children.length > 0 && (!node.isFile);
  const showFiles = node.files.length <= 10 || hasOwnRule;
  return (
    <>
      <tr className="border-t border-slate-100">
        <th scope="row" className="py-2 pr-3 text-left font-normal" style={{ paddingLeft: depth * 16 + 12 }}>
          <span className="flex items-center gap-1.5">
            {expandable ? (
              <button type="button" aria-expanded={open} aria-label={open ? "Collapse" : "Expand"} onClick={() => setOpen(!open)}
                className="rounded p-0.5 text-slate-500 hover:bg-slate-200/70">
                <Icon name="chevron-right" className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-90" : ""}`} />
              </button>
            ) : <span className="w-5" />}
            <Icon name={node.isFile ? "file" : "folder"} className="h-4 w-4 text-slate-400" />
            <span className="truncate text-sm">{node.name}</span>
            <span className="text-xs text-slate-400">{node.isFile ? "" : node.files.length}</span>
          </span>
        </th>
        {members.map((m) => {
          const state = coverage(node, rules, m.id);
          return (
            <td key={m.id} className="px-2 py-1.5 text-center">
              <button
                type="button" disabled={busy} onClick={() => onToggle(node, m, state)}
                aria-label={`${m.name || m.email}: ${state === "all" ? "can read" : state === "partial" ? "can read some" : "cannot read"} ${node.name}. Click to ${state === "all" ? "revoke" : "grant"}.`}
                className={`inline-flex h-7 w-7 items-center justify-center rounded-md border text-xs font-semibold transition disabled:opacity-50 ${
                  state === "all" ? "border-emerald-500/40 bg-emerald-500/20 text-emerald-700"
                  : state === "partial" ? "border-amber-500/40 bg-amber-500/15 text-amber-700"
                  : "border-slate-300 bg-surface text-slate-400 hover:bg-slate-100"
                }`}
              >
                {state === "all" ? <Icon name="check" className="h-4 w-4" /> : state === "partial" ? "~" : ""}
              </button>
            </td>
          );
        })}
      </tr>
      {open && node.children
        .filter((c) => !c.isFile || showFiles)
        .map((c) => <MatrixRows key={c.path} node={c} depth={depth + 1} rules={rules} members={members} onToggle={onToggle} busy={busy} />)}
    </>
  );
}

function Matrix({ projectId }: { projectId: string }) {
  const { access, members, tree, rules } = useAccess(projectId);
  const { save } = useSaveRule(projectId);
  if (access.isLoading) return <Skeleton className="h-64" />;
  if (tree.files.length === 0) return <EmptyState icon="folder" title="No files yet">Upload files to see who can read what.</EmptyState>;
  if (members.length === 0) return <EmptyState icon="folder" title="No members yet">Add people on the People view first.</EmptyState>;

  const memberIds = members.map((m) => m.id);
  const toggle = (node: TreeNode, user: Person, state: "none" | "partial" | "all") => {
    // start from who can read this path today, then add or remove this one person
    const next = new Set(readers(governingRule(rules, node.path), memberIds));
    if (state === "all") next.delete(user.id); else next.add(user.id);
    save.mutate(ruleFor(node.path, [...next]));
  };
  return (
    <div>
      <p className="mb-3 text-sm text-slate-500">
        Click a cell to grant or revoke. <Icon name="check" className="inline h-4 w-4 align-[-3px] text-emerald-700" /> reads everything in it,{" "}
        <span className="font-medium text-amber-700">~</span> reads some (a file inside has its own rule).
      </p>
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="px-3 py-2 font-medium">Folder</th>
              {members.map((m) => (
                <th key={m.id} className="max-w-[7rem] truncate px-2 py-2 text-center font-medium" title={m.email}>{m.name || m.email}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tree.children.map((c) => (
              <MatrixRows key={c.path} node={c} depth={0} rules={rules} members={members} onToggle={toggle} busy={save.isPending} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ---------- Audit log ---------- */
const OUTCOME: Record<AuditRow["outcome"], { label: string; tone: "success" | "neutral" | "danger" }> = {
  served: { label: "Answered", tone: "success" },
  refused: { label: "No relevant info", tone: "neutral" },
  error: { label: "Error", tone: "danger" },
};

function Audit({ projectId }: { projectId: string }) {
  const audit = useQuery({ queryKey: ["access", projectId, "audit"], queryFn: () => accessApi.audit(projectId) });
  if (audit.isLoading) return <Skeleton className="h-40" />;
  if (audit.isError) return <p className="text-red-600">Could not load the audit log.</p>;
  if (!audit.data?.length) {
    return <EmptyState icon="chat" title="No questions yet">Every question asked in this project is listed here, with the files used to answer it.</EmptyState>;
  }
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr><th className="px-4 py-2.5">When</th><th className="px-4 py-2.5">Who</th><th className="px-4 py-2.5">Question</th><th className="px-4 py-2.5">Result</th><th className="px-4 py-2.5">Files used</th></tr>
        </thead>
        <tbody className="divide-y divide-slate-100 align-top">
          {audit.data.map((r) => (
            <tr key={r.id}>
              <td className="whitespace-nowrap px-4 py-2.5 text-xs text-slate-500">{new Date(r.created_at).toLocaleString()}</td>
              <td className="px-4 py-2.5">{r.user_email}</td>
              <td className="max-w-[22rem] px-4 py-2.5"><span className="line-clamp-3 break-words">{r.question}</span></td>
              <td className="px-4 py-2.5"><Badge tone={OUTCOME[r.outcome].tone}>{OUTCOME[r.outcome].label}</Badge></td>
              <td className="px-4 py-2.5 font-mono text-xs">{r.served_paths.length ? r.served_paths.join(", ") : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AccessTab({ projectId }: { projectId: string }) {
  const [view, setView] = useState<View>("People");
  return (
    <div>
      <div className="mb-5 flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-500/10 text-brand-link"><Icon name="lock" className="h-5 w-5" /></span>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Access</h2>
          <p className="mt-1 max-w-3xl text-sm text-slate-500">
            Choose who can read each folder or file. Admins always read everything. A file nobody has a rule for is
            visible to admins only. Use "Preview app as" to check what a person sees before you share.
          </p>
        </div>
      </div>
      <div className="mb-5 max-w-full overflow-x-auto">
        <div role="tablist" className="segmented">
          {VIEWS.map((v) => (
            <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)} className="whitespace-nowrap">
              {v}
            </button>
          ))}
        </div>
      </div>
      {view === "People" && <People projectId={projectId} />}
      {view === "By folder" && <ByFolder projectId={projectId} />}
      {view === "By person" && <ByPerson projectId={projectId} />}
      {view === "Matrix" && <Matrix projectId={projectId} />}
      {view === "Audit log" && <Audit projectId={projectId} />}
    </div>
  );
}
