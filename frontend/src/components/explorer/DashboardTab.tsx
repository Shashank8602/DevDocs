import { useQuery } from "@tanstack/react-query";
import { insightsApi } from "../../api/insights";
import MarkdownRenderer from "../viewer/MarkdownRenderer";
import { Badge, FileIcon, Icon, Skeleton } from "../ui";

type IconName = Parameters<typeof Icon>[0]["name"];

const TYPE_LABEL = { source: "Source code", doc: "Documentation", other: "Other" } as const;
const TYPE_ICON = { source: "code", doc: "book", other: "folder" } as const;
// brand first, the teal accent second, then distinct mid-tones that read on light and dark surfaces
const PALETTE = ["bg-brand-500", "bg-accent-500", "bg-amber-500", "bg-sky-500", "bg-rose-500", "bg-slate-400"];

function Stat({ label, value, icon }: { label: string; value: number; icon: IconName }) {
  return (
    <div className="card flex items-center gap-3.5 p-4">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-500/10 text-brand-link">
        <Icon name={icon} className="h-5 w-5" />
      </span>
      <div className="min-w-0">
        <p className="text-2xl font-semibold leading-7 tabular-nums tracking-tight text-slate-900">{value.toLocaleString()}</p>
        <p className="truncate text-xs text-slate-500">{label}</p>
      </div>
    </div>
  );
}

function Section({ title, hint, icon, children }: { title: string; hint?: string; icon: IconName; children: React.ReactNode }) {
  return (
    <section className="card overflow-hidden">
      <header className="flex items-start gap-3 px-5 pb-1 pt-4">
        <Icon name={icon} className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
          {hint && <p className="mt-0.5 truncate text-xs text-slate-500" title={hint}>{hint}</p>}
        </div>
      </header>
      <div className="px-5 pb-5 pt-3 text-sm text-slate-700">{children}</div>
    </section>
  );
}

const Path = ({ path, line }: { path: string; line?: number }) => (
  <span className="selectable-text break-all font-mono text-xs text-slate-600">{path}{line ? <span className="text-slate-400">:{line}</span> : ""}</span>
);

const AskButton = ({ onClick }: { onClick: () => void }) => (
  <button className="btn-ghost shrink-0 px-2 py-1 text-xs text-brand-link" onClick={onClick}>
    <Icon name="chat" className="h-3.5 w-3.5" /> Ask
  </button>
);

function formatSize(bytes: number) {
  return bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
}

/** What DevDocs knows about the project, computed from the files the signed-in person can read. No AI calls. */
export default function DashboardTab({ projectId, onAsk }: { projectId: string; onAsk: (question: string) => void }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["projects", projectId, "dashboard"],
    queryFn: () => insightsApi.dashboard(projectId),
  });
  if (isError) return <p role="alert" className="text-sm text-red-600">Could not load the dashboard.</p>;
  if (isLoading || !data) {
    return (
      <div className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-[4.5rem] rounded-xl" />)}</div>
        <div className="grid gap-5 lg:grid-cols-2"><Skeleton className="h-40 rounded-xl" /><Skeleton className="h-40 rounded-xl" /></div>
      </div>
    );
  }

  const top = data.languages.slice(0, 5).map((l) => ({ name: l.language ?? "", count: l.count }));
  const rest = data.languages.slice(5).reduce((n, l) => n + l.count, 0);
  const segments = rest ? [...top, { name: "other languages", count: rest }] : top;
  const langTotal = segments.reduce((n, s) => n + s.count, 0);

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Stat label="Files you can read" value={data.file_count} icon="file" />
        <Stat label="Indexed passages" value={data.chunk_count} icon="layers" />
        {(Object.keys(TYPE_LABEL) as (keyof typeof TYPE_LABEL)[]).map((k) => (
          <Stat key={k} label={TYPE_LABEL[k]} value={data.by_type[k]} icon={TYPE_ICON[k]} />
        ))}
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Section title="Languages" icon="code">
          {langTotal === 0 ? <p className="text-sm text-slate-500">No source languages detected.</p> : (
            <>
              <div className="flex h-2.5 gap-0.5 overflow-hidden rounded-full" role="img" aria-label="Language breakdown">
                {segments.map((s, i) => (
                  <div key={s.name} className={`${PALETTE[i % PALETTE.length]} first:rounded-l-full last:rounded-r-full`} style={{ width: `${(s.count / langTotal) * 100}%` }} title={`${s.name}: ${s.count}`} />
                ))}
              </div>
              <ul className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
                {segments.map((s, i) => (
                  <li key={s.name} className="flex items-center gap-2">
                    <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${PALETTE[i % PALETTE.length]}`} />
                    <span className="min-w-0 flex-1 truncate text-slate-700">{s.name}</span>
                    <span className="tabular-nums text-xs text-slate-500">{s.count} · {Math.round((s.count / langTotal) * 100)}%</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Section>

        <Section title="Detected stack" icon="cpu" hint="Read from package.json, requirements.txt, docker-compose.yml and similar files.">
          {data.stack.length === 0 ? <p className="text-sm text-slate-500">No manifest files found.</p> : (
            <ul className="flex flex-wrap gap-2">
              {data.stack.map((s) => (
                <li key={s.name} title={`from ${s.source}`} className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-700">
                  {s.name}
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      {data.readme && (
        <Section title="README" icon="book" hint={data.readme.path}>
          <div className="max-h-72 overflow-auto rounded-lg border border-slate-200 bg-slate-50/60">
            <MarkdownRenderer text={data.readme.excerpt} className="px-4 py-3" />
          </div>
        </Section>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Section title="Entry points" icon="arrow-right" hint="Where the program starts.">
          {data.entry_points.length === 0 ? <p className="text-sm text-slate-500">None found.</p> : (
            <ul className="-mx-2 space-y-0.5">
              {data.entry_points.map((e) => (
                <li key={e.path} className="group flex items-start justify-between gap-3 rounded-lg px-2 py-1.5 hover:bg-slate-100/70">
                  <div className="flex min-w-0 gap-2.5">
                    <FileIcon name={e.path} className="mt-0.5 h-4 w-4" />
                    <div className="min-w-0"><Path path={e.path} /><p className="text-xs text-slate-500">{e.reason}</p></div>
                  </div>
                  <AskButton onClick={() => onAsk(`What does ${e.path} do and how does the program start?`)} />
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Largest files" icon="database">
          <ul className="space-y-2">
            {data.largest.map((f) => {
              const pct = data.largest[0]?.size ? (f.size / data.largest[0].size) * 100 : 0;
              return (
                <li key={f.path}>
                  <div className="flex items-center justify-between gap-3"><Path path={f.path} /><span className="shrink-0 text-xs tabular-nums text-slate-500">{formatSize(f.size)}</span></div>
                  <div className="mt-1 h-1 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-accent-500/70" style={{ width: `${pct}%` }} /></div>
                </li>
              );
            })}
          </ul>
        </Section>
      </div>

      <Section title="Configuration" icon="sliders" hint="Environment variables the code reads, and keys listed in .env.example files.">
        {data.env_vars.length === 0 ? <p className="text-sm text-slate-500">No environment variables found.</p> : (
          <div className="max-h-72 overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-surface text-left text-xs text-slate-500"><tr className="border-b border-slate-200"><th className="py-2 pr-3 font-medium">Variable</th><th className="py-2 pr-3 font-medium">First seen</th><th className="py-2" /></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {data.env_vars.map((v) => (
                  <tr key={v.name} className="group hover:bg-slate-100/50">
                    <td className="py-2 pr-3"><span className="selectable-text rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-800">{v.name}</span></td>
                    <td className="py-2 pr-3"><Path path={v.path} line={v.line} /></td>
                    <td className="whitespace-nowrap py-2 text-right">
                      {v.declared && <Badge tone="success">in .env.example</Badge>}{" "}
                      <AskButton onClick={() => onAsk(`How is ${v.name} used?`)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <p className="flex items-center justify-center gap-1.5 pb-2 text-xs text-slate-500"><Icon name="check" className="h-3.5 w-3.5 text-emerald-700" /> Computed from your files. No AI quota used.</p>
    </div>
  );
}
