import { apiFetch } from "./http";

export interface Dashboard {
  file_count: number;
  chunk_count: number;
  by_type: { doc: number; source: number; other: number };
  languages: { language: string | null; count: number }[];
  stack: { name: string; source: string }[];
  entry_points: { path: string; reason: string }[];
  env_vars: { name: string; path: string; line: number; declared: boolean }[];
  largest: { path: string; size: number }[];
  readme: { path: string; excerpt: string } | null;
}

export interface SearchHit {
  file_id: string;
  path: string;
  language: string | null;
  file_type: "doc" | "source" | "other";
  line_start: number | null;
  line_end: number | null;
  page_number: number | null;
  section: string | null;
  snippet: string;
  score: number;
}

export interface SymbolItem {
  name: string;
  kind: string;
  path: string;
  line: number;
  file_id: string;
}

export interface GraphData {
  nodes: { id: string; path: string; language: string | null; imports: number; imported_by: number }[];
  edges: { source: string; target: string }[];
  files_without_links: number;
  note: string;
}

export interface Impact {
  path: string;
  imports: string[];
  imported_by: string[];
  affected: string[];
}

async function get<T>(path: string): Promise<T> {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error(res.status === 503 ? "Search is unavailable right now." : "Something went wrong. Please try again.");
  return res.json();
}

export const insightsApi = {
  dashboard: (projectId: string) => get<Dashboard>(`/projects/${projectId}/dashboard`),
  starters: (projectId: string) => get<{ questions: string[] }>(`/projects/${projectId}/starters`),
  symbols: (projectId: string, q: string) =>
    get<SymbolItem[]>(`/projects/${projectId}/symbols?q=${encodeURIComponent(q)}`),
  search: (projectId: string, q: string, filters: { language?: string; file_type?: string; folder?: string }) => {
    const p = new URLSearchParams({ q });
    for (const [k, v] of Object.entries(filters)) if (v) p.set(k, v);
    return get<{ query: string; hits: SearchHit[]; searched_files: number }>(`/projects/${projectId}/search?${p}`);
  },
  graph: (projectId: string) => get<GraphData>(`/projects/${projectId}/graph`),
  impact: (projectId: string, fileId: string) => get<Impact>(`/projects/${projectId}/graph/${fileId}`),
};
