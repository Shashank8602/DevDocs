import { useQuery } from "@tanstack/react-query";
import { useLayoutEffect, useMemo, useState } from "react";
import { GraphData, insightsApi } from "../../api/insights";
import { EmptyState, FileIcon, Icon, Skeleton } from "../ui";

/* ---------- geometry ---------- */
const NODE_W = 220;
const NODE_H = 40;
const ROW_GAP = 10;
const COL_GAP = 110;
const PAD = 16;
const MAX_FILE_NODES = 60; // a folder view shows its files plus their nearest neighbours, capped
// mid-tone group colours: legible as strokes and tints on both the light and the dark surface
const COLORS = ["#14b8a6", "#f59e0b", "#3b82f6", "#f43f5e", "#8b5cf6", "#22c55e", "#f97316", "#06b6d4"];
// everything else follows the theme tokens (SVG attributes cannot read CSS variables, inline styles can)
const T = {
  surface: "rgb(var(--surface))",
  text: "rgb(var(--slate-900))",
  muted: "rgb(var(--slate-500))",
  edge: "rgb(var(--slate-400))",
  active: "rgb(var(--accent-600))",
};
const TEST_PATH = /(^|\/)(tests?|__tests__|spec)(\/|$)|(^|\/)(test_[^/]+|[^/]+\.(test|spec)\.[a-z]+)$/i;

interface VNode {
  key: string; // file id, or the folder name in the folder view
  label: string;
  sub: string; // second line: the folder of a file, or "N files"
  group: string;
  paths: string[];
  fileId?: string;
  x: number;
  y: number;
}
interface VLink { a: number; b: number; weight: number; back: boolean }

/** Folder name used to group files: the shallowest depth that gives a readable number of groups. */
function groupFn(paths: string[]): (p: string) => string {
  const split = paths.map((p) => p.split("/"));
  let strip = 0; // a single shared top folder (the uploaded project's own name) says nothing
  while (split.length && split.every((s) => s.length > strip + 1 && s[strip] === split[0][strip])) strip++;
  const at = (depth: number) => (p: string) => {
    const dirs = p.split("/").slice(strip, -1);
    return dirs.slice(0, depth).join("/") || "(top level)";
  };
  for (let depth = 3; depth >= 1; depth--) {
    if (new Set(paths.map(at(depth))).size <= 24) return at(depth);
  }
  return at(1);
}

/** Left-to-right layers: a file sits right of everything that imports it. Cycles are broken, not drawn through. */
function layered(nodes: VNode[], rawLinks: { a: number; b: number; weight: number }[]): { links: VLink[]; width: number; height: number } {
  const n = nodes.length;
  const out: number[][] = Array.from({ length: n }, () => []);
  rawLinks.forEach((l) => out[l.a].push(l.b));
  const state = new Array(n).fill(0); // 0 new, 1 on the stack, 2 done
  const back = new Set<string>();
  const dfs = (u: number) => {
    state[u] = 1;
    for (const v of out[u]) {
      if (state[v] === 1) back.add(`${u}-${v}`);
      else if (state[v] === 0) dfs(v);
    }
    state[u] = 2;
  };
  for (let i = 0; i < n; i++) if (state[i] === 0) dfs(i);
  const links: VLink[] = rawLinks.map((l) => ({ ...l, back: back.has(`${l.a}-${l.b}`) }));

  const layer = new Array(n).fill(0);
  const indeg = new Array(n).fill(0);
  const dag = links.filter((l) => !l.back);
  dag.forEach((l) => indeg[l.b]++);
  const queue = [...Array(n).keys()].filter((i) => indeg[i] === 0);
  while (queue.length) {
    const u = queue.shift()!;
    for (const l of dag) if (l.a === u) {
      layer[l.b] = Math.max(layer[l.b], layer[u] + 1);
      if (--indeg[l.b] === 0) queue.push(l.b);
    }
  }
  const columns: number[][] = [];
  nodes.forEach((_, i) => (columns[layer[i]] ??= []).push(i));
  columns.forEach((col) => col.sort((x, y) => nodes[x].group.localeCompare(nodes[y].group) || nodes[x].label.localeCompare(nodes[y].label)));

  // a few barycentre sweeps to cut crossings
  const rowOf = new Array(n).fill(0);
  const reindex = () => columns.forEach((col) => col.forEach((id, r) => (rowOf[id] = r)));
  reindex();
  for (let sweep = 0; sweep < 4; sweep++) {
    const order = sweep % 2 === 0 ? columns.map((_, i) => i) : columns.map((_, i) => columns.length - 1 - i);
    for (const c of order) {
      const score = new Map<number, number>();
      columns[c].forEach((id) => {
        const near = dag.filter((l) => (sweep % 2 === 0 ? l.b === id : l.a === id)).map((l) => rowOf[sweep % 2 === 0 ? l.a : l.b]);
        score.set(id, near.length ? near.reduce((s, v) => s + v, 0) / near.length : rowOf[id]);
      });
      columns[c].sort((x, y) => score.get(x)! - score.get(y)!);
      reindex();
    }
  }
  nodes.forEach((node, i) => {
    node.x = PAD + layer[i] * (NODE_W + COL_GAP);
    node.y = PAD + rowOf[i] * (NODE_H + ROW_GAP);
  });
  return {
    links,
    width: PAD * 2 + columns.length * NODE_W + Math.max(columns.length - 1, 0) * COL_GAP,
    height: PAD * 2 + Math.max(...columns.map((c) => c.length), 1) * (NODE_H + ROW_GAP),
  };
}

const STEP = NODE_H + ROW_GAP;
const MAX_ZOOM = 1.3; // a small map may be drawn up to this much larger; text stays crisp and readable

/**
 * Spreads a finished layout over the space the card really has, so a small map is not a corner of an empty card.
 * Columns get wider gaps, each column is centred top to bottom, and the whole drawing may grow up to MAX_ZOOM.
 * A map that is already bigger than the card is left alone (the card scrolls). Order and links are unchanged.
 */
export function fit(nodes: VNode[], natural: { width: number; height: number }, box: { w: number; h: number }) {
  if (!box.w || !box.h || nodes.length === 0) return { nodes, scale: 1, width: natural.width, height: natural.height };
  const scale = Math.min(MAX_ZOOM, Math.max(1, Math.min(box.w / natural.width, box.h / natural.height)));
  const ew = box.w / scale, eh = box.h / scale;
  const xs = [...new Set(nodes.map((n) => n.x))].sort((a, b) => a - b);
  const gapExtra = xs.length > 1 && ew > natural.width ? Math.min((ew - natural.width) / (xs.length - 1), COL_GAP * 2.5) : 0;
  const contentW = natural.width + gapExtra * (xs.length - 1);
  const offX = Math.max(0, (ew - contentW) / 2);
  const cols = new Map<number, VNode[]>();
  nodes.forEach((n) => cols.set(n.x, [...(cols.get(n.x) ?? []), n].sort((a, b) => a.y - b.y)));
  const maxRows = Math.max(...[...cols.values()].map((c) => c.length));
  const stepY = Math.max(STEP, Math.min(STEP * 2.4, (eh - PAD * 2 - NODE_H) / Math.max(maxRows - 1, 1)));
  const placed = nodes.map((n) => {
    const col = cols.get(n.x)!;
    const colH = (col.length - 1) * stepY + NODE_H;
    return { ...n, x: offX + PAD + xs.indexOf(n.x) * (NODE_W + COL_GAP + gapExtra), y: Math.max(PAD, (eh - colH) / 2) + col.indexOf(n) * stepY };
  });
  return {
    nodes: placed, scale,
    width: Math.max(box.w, Math.ceil((offX + contentW) * scale)),
    height: Math.max(box.h, Math.ceil((Math.max(...placed.map((p) => p.y)) + NODE_H + PAD) * scale)),
  };
}

/** The size of an element, kept current. Unknown (0) until measured, and in test environments without ResizeObserver. */
function useBox() {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    if (!el || typeof ResizeObserver === "undefined") return;
    const read = () => setBox({ w: Math.max(el.clientWidth - 1, 0), h: Math.max(el.clientHeight - 1, 0) });
    read();
    const watch = new ResizeObserver(read);
    watch.observe(el);
    return () => watch.disconnect();
  }, [el]);
  return [setEl, box] as const;
}

const base = (p: string) => p.split("/").pop() ?? p;
const dir = (p: string) => p.split("/").slice(0, -1).join("/");

/* ---------- component ---------- */
export default function GraphTab({
  projectId, onAsk, onOpenFile,
}: { projectId: string; onAsk: (q: string) => void; onOpenFile: (fileId: string) => void }) {
  const { data, isLoading, isError } = useQuery({ queryKey: ["projects", projectId, "graph"], queryFn: () => insightsApi.graph(projectId) });
  const [hideTests, setHideTests] = useState(true);
  const [folder, setFolder] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [find, setFind] = useState("");
  const [cardRef, box] = useBox();

  const visible = useMemo(() => {
    if (!data) return null;
    const keep = data.nodes.filter((n) => !hideTests || !TEST_PATH.test(n.path));
    const ids = new Set(keep.map((n) => n.id));
    return { ...data, nodes: keep, edges: data.edges.filter((e) => ids.has(e.source) && ids.has(e.target)) } as GraphData;
  }, [data, hideTests]);

  const groupOf = useMemo(() => groupFn(visible?.nodes.map((n) => n.path) ?? []), [visible]);
  const byId = useMemo(() => new Map(visible?.nodes.map((n) => [n.id, n]) ?? []), [visible]);

  const view = useMemo(() => {
    if (!visible) return null;
    let nodes: VNode[] = [];
    let raw: { a: number; b: number; weight: number }[] = [];
    if (folder === null) {
      const groups = new Map<string, string[]>();
      visible.nodes.forEach((n) => groups.set(groupOf(n.path), [...(groups.get(groupOf(n.path)) ?? []), n.path]));
      nodes = [...groups].map(([g, paths]) => ({ key: g, label: g, sub: `${paths.length} file${paths.length === 1 ? "" : "s"}`, group: g, paths, x: 0, y: 0 }));
      const idx = new Map(nodes.map((n, i) => [n.key, i]));
      const weight = new Map<string, number>();
      for (const e of visible.edges) {
        const a = idx.get(groupOf(byId.get(e.source)!.path))!, b = idx.get(groupOf(byId.get(e.target)!.path))!;
        if (a !== b) weight.set(`${a}-${b}`, (weight.get(`${a}-${b}`) ?? 0) + 1);
      }
      raw = [...weight].map(([k, w]) => ({ a: Number(k.split("-")[0]), b: Number(k.split("-")[1]), weight: w }));
    } else {
      const inside = new Set(visible.nodes.filter((n) => groupOf(n.path) === folder).map((n) => n.id));
      const near = new Set<string>();
      for (const e of visible.edges) {
        if (inside.has(e.source) && !inside.has(e.target)) near.add(e.target);
        if (inside.has(e.target) && !inside.has(e.source)) near.add(e.source);
      }
      const pick = [...inside, ...near].slice(0, MAX_FILE_NODES);
      const keep = new Set(pick);
      nodes = pick.map((id) => {
        const f = byId.get(id)!;
        return { key: id, label: base(f.path), sub: dir(f.path), group: groupOf(f.path), paths: [f.path], fileId: id, x: 0, y: 0 };
      });
      const idx = new Map(nodes.map((n, i) => [n.key, i]));
      raw = visible.edges.filter((e) => keep.has(e.source) && keep.has(e.target))
        .map((e) => ({ a: idx.get(e.source)!, b: idx.get(e.target)!, weight: 1 }));
    }
    const geo = layered(nodes, raw);
    return { nodes, ...geo, capped: folder !== null && visible.nodes.length > 0 && nodes.length === MAX_FILE_NODES };
  }, [visible, folder, groupOf, byId]);

  const selNode = view?.nodes.find((n) => n.key === selected) ?? null;
  const impact = useQuery({
    queryKey: ["projects", projectId, "graph", selNode?.fileId],
    queryFn: () => insightsApi.impact(projectId, selNode!.fileId!),
    enabled: !!selNode?.fileId,
  });

  if (isLoading) return <Skeleton className="h-96" />;
  if (isError || !data || !visible || !view) return <p role="alert" className="text-sm text-red-600">Could not load the import map.</p>;
  if (data.nodes.length === 0) {
    return (
      <EmptyState icon="layers" title="No imports to draw">
        No file you can read imports another project file in a language DevDocs understands (Python, JavaScript, TypeScript, Java, C).
      </EmptyState>
    );
  }

  const groups = [...new Set(visible.nodes.map((n) => groupOf(n.path)))].sort();
  const color = (g: string) => COLORS[groups.indexOf(g) % COLORS.length] ?? COLORS[0];
  const selIndex = selNode ? view.nodes.indexOf(selNode) : -1;
  const related = new Set<number>();
  view.links.forEach((l) => { if (l.a === selIndex) related.add(l.b); if (l.b === selIndex) related.add(l.a); });

  const focusPath = (path: string) => {
    const file = data.nodes.find((n) => n.path === path);
    if (!file) return;
    if (hideTests && TEST_PATH.test(path)) setHideTests(false);
    setFolder(groupFn(data.nodes.map((n) => n.path))(path));
    setSelected(file.id);
  };
  const matches = find.trim().length > 1 ? data.nodes.filter((n) => n.path.toLowerCase().includes(find.trim().toLowerCase())).slice(0, 8) : [];

  const folderLinks = selNode && !selNode.fileId ? {
    uses: view.links.filter((l) => l.a === selIndex).map((l) => [view.nodes[l.b].label, l.weight] as const),
    usedBy: view.links.filter((l) => l.b === selIndex).map((l) => [view.nodes[l.a].label, l.weight] as const),
  } : null;

  const drawn = fit(view.nodes, view, box);

  return (
    <div className="flex h-full min-h-[28rem] flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <nav aria-label="Map level" className="flex items-center gap-1.5 text-sm">
          <button
            className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 transition ${folder === null ? "font-semibold text-slate-900" : "text-brand-link hover:bg-brand-500/10"}`}
            onClick={() => { setFolder(null); setSelected(null); }}
          >
            <Icon name="graph" className="h-4 w-4" /> All folders
          </button>
          {folder !== null && (
            <>
              <Icon name="chevron-right" className="h-3.5 w-3.5 text-slate-400" />
              <span className="flex items-center gap-1.5 rounded-lg bg-slate-100 px-2.5 py-1 font-mono text-xs font-semibold text-slate-800">
                <Icon name="folder-open" className="h-3.5 w-3.5 text-amber-500" />{folder}
              </span>
            </>
          )}
        </nav>
        <label className="relative ml-auto min-w-[14rem]">
          <span className="sr-only">Find a file</span>
          <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a file…" className="input py-1.5 pl-9 text-sm" />
          <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          {matches.length > 0 && (
            <ul className="card absolute right-0 z-20 mt-1.5 max-h-64 w-full min-w-[22rem] animate-scale-in overflow-auto p-1 shadow-lift">
              {matches.map((m) => (
                <li key={m.id}>
                  <button className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left font-mono text-xs text-slate-700 hover:bg-slate-100" onClick={() => { focusPath(m.path); setFind(""); }}>
                    <FileIcon name={m.path} className="h-3.5 w-3.5" /><span className="truncate">{m.path}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={hideTests} onChange={(e) => setHideTests(e.target.checked)} className="h-4 w-4 accent-[rgb(var(--brand-600))]" /> Hide test files
        </label>
      </div>

      <div className="grid min-h-0 flex-1 gap-5 lg:grid-cols-[minmax(0,1fr)_19rem]">
        <div className="flex min-h-[24rem] min-w-0 flex-col">
          <div ref={cardRef} className="card min-h-0 flex-1 overflow-auto">
            <svg width={drawn.width} height={drawn.height} className="block" role="img" aria-label="Import map: arrows point from a file to what it imports">
              <defs>
                <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
                  <path d="M0 0L10 5L0 10z" style={{ fill: T.edge }} />
                </marker>
                <marker id="arrow-active" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
                  <path d="M0 0L10 5L0 10z" style={{ fill: T.active }} />
                </marker>
                <pattern id="map-dots" width="20" height="20" patternUnits="userSpaceOnUse">
                  <circle cx="1" cy="1" r="1" style={{ fill: "rgb(var(--slate-300))" }} opacity="0.6" />
                </pattern>
              </defs>
              <rect width="100%" height="100%" fill="url(#map-dots)" />
              <g transform={`scale(${drawn.scale})`}>
              {view.links.map((l, i) => {
                const a = drawn.nodes[l.a], b = drawn.nodes[l.b];
                const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2;
                const active = selIndex >= 0 && (l.a === selIndex || l.b === selIndex);
                const mid = Math.max((x2 - x1) / 2, 30);
                const d = l.back
                  ? `M${a.x + NODE_W / 2} ${a.y + NODE_H} C${a.x + NODE_W / 2} ${a.y + NODE_H + 40} ${b.x + NODE_W / 2} ${b.y + NODE_H + 40} ${b.x + NODE_W / 2} ${b.y + NODE_H}`
                  : `M${x1} ${y1} C${x1 + mid} ${y1} ${x2 - mid} ${y2} ${x2 - 2} ${y2}`;
                return (
                  <path
                    key={i} d={d} fill="none" strokeDasharray={l.back ? "4 3" : undefined}
                    style={{ stroke: active ? T.active : T.edge }} strokeWidth={active ? 2 : 1 + Math.min(Math.log2(l.weight), 2) * 0.6}
                    opacity={selIndex >= 0 && !active ? 0.12 : active ? 1 : 0.6} markerEnd={active ? "url(#arrow-active)" : "url(#arrow)"}
                  >
                    <title>{`${a.label} imports ${b.label}${l.weight > 1 ? ` (${l.weight} imports)` : ""}${l.back ? " (circular)" : ""}`}</title>
                  </path>
                );
              })}
              {drawn.nodes.map((n, i) => {
                const dim = selIndex >= 0 && i !== selIndex && !related.has(i);
                const on = i === selIndex;
                return (
                  <g
                    key={n.key} transform={`translate(${n.x} ${n.y})`} opacity={dim ? 0.35 : 1} className="cursor-pointer"
                    // a double-click is two clicks first: the second must not undo the selection the first made
                    onClick={(e) => e.detail < 2 && setSelected(on ? null : n.key)}
                    // same as the side panel buttons: a folder shows its files, a file opens
                    onDoubleClick={() => (n.fileId ? onOpenFile(n.fileId) : (setFolder(n.key), setSelected(null)))}
                    role="button" tabIndex={0} aria-pressed={on}
                    aria-label={`${n.label}, ${n.sub}`}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(on ? null : n.key); } }}
                  >
                    <rect width={NODE_W} height={NODE_H} rx={8} style={{ fill: T.surface }} />
                    <rect
                      width={NODE_W} height={NODE_H} rx={8}
                      style={{ fill: color(n.group), stroke: color(n.group) }}
                      fillOpacity={on ? 0.22 : 0.07} strokeWidth={on ? 2.5 : 1.25}
                    />
                    <circle cx={NODE_W - 14} cy={NODE_H / 2} r={3.5} style={{ fill: color(n.group) }} />
                    <text x={12} y={17} fontSize="13" fontWeight="600" style={{ fill: T.text }}>{n.label.length > 25 ? `${n.label.slice(0, 24)}…` : n.label}</text>
                    <text x={12} y={32} fontSize="12" style={{ fill: T.muted }}>{n.sub.length > 28 ? `…${n.sub.slice(-27)}` : n.sub || "top level"}</text>
                    <title>{n.paths.length === 1 ? n.paths[0] : `${n.label} (${n.paths.length} files)`}</title>
                  </g>
                );
              })}
              </g>
            </svg>
          </div>
          <p className="mt-2 shrink-0 text-xs text-slate-500">
            Arrows point from a file to what it imports; files on the left use files on the right. {folder === null ? "Double-click a folder to show its files." : "Double-click a file to open it."}
            {view.capped ? ` Showing the first ${MAX_FILE_NODES} files and their neighbours.` : ""} {data.files_without_links} code files have no links. {data.note}
          </p>
        </div>

        <aside className="card min-h-0 self-stretch overflow-auto" aria-live="polite">
          {!selNode ? (
            <div className="flex flex-col items-center px-6 py-10 text-center text-sm text-slate-500">
              <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-brand-500/10 text-brand-link"><Icon name="graph" className="h-5 w-5" /></span>
              <p className="mt-3 font-semibold text-slate-800">Nothing selected</p>
              <p className="mt-1">Select a {folder === null ? "folder" : "file"} to see what it imports, what imports it, and what might break if it changes.</p>
            </div>
          ) : (
            <>
              <header className="flex items-start gap-3 border-b border-slate-200 px-4 py-3.5">
                <span
                  className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
                  style={{ background: `${color(selNode.group)}22`, color: color(selNode.group) }}
                >
                  <Icon name={selNode.fileId ? "file" : "folder"} className="h-[18px] w-[18px]" />
                </span>
                <div className="min-w-0">
                  <p className="text-xs font-medium text-slate-500">{selNode.fileId ? "File" : "Folder"}</p>
                  <h3 className="break-all text-base font-semibold leading-6 text-slate-900">{selNode.label}</h3>
                  <p className="mt-0.5 break-all font-mono text-xs text-slate-500">{selNode.fileId ? selNode.paths[0] : `${selNode.paths.length} files`}</p>
                </div>
              </header>
              <div className="space-y-4 p-4">
                {selNode.fileId && impact.data && (
                  <>
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <Stat n={impact.data.imports.length} label="Imports" />
                      <Stat n={impact.data.imported_by.length} label="Imported by" />
                      <Stat n={impact.data.affected.length} label="May break" />
                    </div>
                    <Group title="Imports" hint="Files this file uses" items={impact.data.imports} onPick={focusPath} />
                    <Group title="Imported by" hint="Files that use this file" items={impact.data.imported_by} onPick={focusPath} />
                    <Group title="Might break if it changes" hint="Direct and indirect users" items={impact.data.affected} onPick={focusPath} />
                  </>
                )}
                {selNode.fileId && impact.isLoading && <Skeleton className="h-24" />}
                {folderLinks && (
                  <>
                    <PairGroup title="Uses these folders" items={folderLinks.uses} />
                    <PairGroup title="Used by these folders" items={folderLinks.usedBy} />
                    <Group title="Files in this folder" items={selNode.paths.slice(0, 40)} onPick={focusPath} />
                  </>
                )}
                <div className="flex flex-col gap-2">
                  {!selNode.fileId && (
                    <button className="btn-primary w-full" onClick={() => { setFolder(selNode.key); setSelected(null); }}>Show files in this folder</button>
                  )}
                  {selNode.fileId && <button className="btn-secondary w-full" onClick={() => onOpenFile(selNode.fileId!)}>Open file</button>}
                  <button className="btn-secondary w-full" onClick={() => onAsk(selNode.fileId ? `What does ${selNode.paths[0]} do, and what depends on it?` : `What is the ${selNode.label} folder responsible for?`)}>
                    <Icon name="chat" /> Ask about this {selNode.fileId ? "file" : "folder"}
                  </button>
                </div>
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-2">
      <p className="text-lg font-semibold tabular-nums text-slate-900">{n}</p>
      <p className="text-xs text-slate-500">{label}</p>
    </div>
  );
}

function Group({ title, hint, items, onPick }: { title: string; hint?: string; items: string[]; onPick: (p: string) => void }) {
  return (
    <section>
      <h4 className="text-sm font-semibold text-slate-800">{title} <span className="font-normal tabular-nums text-slate-400">{items.length}</span></h4>
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
      {items.length === 0 ? <p className="mt-1 text-sm text-slate-400">None</p> : (
        <ul className="mt-1.5 max-h-44 list-disc space-y-1 overflow-auto pl-5 marker:text-slate-400">
          {items.map((p) => (
            <li key={p} className="text-sm">
              <button className="break-all text-left font-mono text-xs text-brand-link hover:underline" onClick={() => onPick(p)} title={p}>
                {base(p)}<span className="ml-1.5 font-sans text-xs text-slate-400">{dir(p)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function PairGroup({ title, items }: { title: string; items: readonly (readonly [string, number])[] }) {
  return (
    <section>
      <h4 className="text-sm font-semibold text-slate-800">{title} <span className="font-normal tabular-nums text-slate-400">{items.length}</span></h4>
      {items.length === 0 ? <p className="mt-1 text-sm text-slate-400">None</p> : (
        <ul className="mt-1.5 list-disc space-y-1 pl-5 marker:text-slate-400">
          {items.map(([name, n]) => <li key={name} className="text-sm"><span className="font-mono text-xs">{name}</span> <span className="text-xs text-slate-400">{n} import{n === 1 ? "" : "s"}</span></li>)}
        </ul>
      )}
    </section>
  );
}
