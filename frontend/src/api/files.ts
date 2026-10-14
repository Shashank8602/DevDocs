const BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export interface UploadRules {
  ignored_names: string[];
  max_file_bytes: number;
  max_total_bytes: number;
  extensions: Record<string, { type: "doc" | "source" | "other"; language: string | null }>;
}

export interface ProjectFile {
  id: string;
  file_path: string;
  file_name: string;
  file_type: "doc" | "source" | "other";
  language: string | null;
  file_size: number;
  status: "pending" | "processing" | "ready" | "failed";
  updated_at: string;
}

export interface ChunkInfo {
  id: string;
  ordinal: number;
  content: string;
  line_start: number | null;
  line_end: number | null;
  page_number: number | null;
  section: string | null;
  token_count: number;
}

export interface ActiveIngestion {
  project_id: string;
  project_name: string;
  remaining: number;
  total: number;
}

export interface UploadReport {
  accepted: number;
  rejected: number;
  results: { path: string; status: "accepted" | "rejected"; reason: string | null }[];
  created: string[];
  modified: string[];
  deleted: string[];
  unchanged: string[];
}

export type UploadChanges = Pick<UploadReport, "created" | "modified" | "deleted" | "unchanged">;

export interface ProjectOverview {
  file_count: number;
  by_type: { doc: number; source: number; other: number };
  languages: { language: string | null; count: number }[];
  last_updated: string | null;
}

export interface ESInspector {
  index: string;
  totals: { postgres: number; es: number };
  files: { file_id: string; path: string; status: ProjectFile["status"]; postgres_chunks: number; es_chunks: number }[];
  orphans: { file_id: string; es_chunks: number }[];
}

export interface VectorPeekChunk {
  chunk_id: string;
  ordinal: number;
  line_start: number | null;
  line_end: number | null;
  page_number: number | null;
  section: string | null;
  token_count: number;
  content_preview: string;
  dims: number;
  norm: number;
  min: number;
  max: number;
  preview: number[];
  values: number[] | null;
}

export interface VectorPeekResult {
  index: string;
  file_path: string;
  file_status: string;
  postgres_chunks: number;
  chunks: VectorPeekChunk[];
}

export interface PlaygroundHit {
  rank: number;
  chunk_id: string;
  file_id: string;
  file_path: string;
  score: number;
  content: string;
  line_start: number | null;
  line_end: number | null;
  section: string | null;
}

export interface PipelineHit {
  rank: number;
  chunk_id: string;
  file_id: string;
  file_path: string;
  content: string;
  line_start: number | null;
  line_end: number | null;
  page_number: number | null;
  section: string | null;
  token_count: number;
  rrf_score: number;
  bm25_rank: number | null;
  vector_rank: number | null;
  vector_score: number | null;
  rerank_score: number | null;
  kept: boolean;
  dropped_by: "gate" | "relative" | "threshold" | "overlap" | "top_k" | "budget" | null;
}

export interface PipelineResult {
  index: string;
  query: string;
  ready_files: number;
  gate_threshold: number;
  top_vector_score: number | null;
  gate_passed: boolean;
  relative_cutoff: number;
  bm25_protect: number;
  reranked: boolean;
  bm25: PlaygroundHit[];
  vector: PlaygroundHit[];
  fused: number;
  candidates: PipelineHit[];
  final: PipelineHit[];
  context_tokens: number;
  token_budget: number;
  top_k: number;
  no_relevant_context: boolean;
  error: string | null;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) throw new Error(res.status === 409 ? "The project is being processed." : "Something went wrong. Please try again.");
  return res.status === 204 ? (undefined as T) : res.json();
}

export const filesApi = {
  rules: () => json<UploadRules>("/upload-rules"),
  list: (projectId: string) => json<ProjectFile[]>(`/projects/${projectId}/files`),
  remove: (fileId: string) => json<void>(`/files/${fileId}`, { method: "DELETE" }),
  replace: (fileId: string, file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return json<{ id: string; changed: boolean }>(`/files/${fileId}`, { method: "PUT", body: form });
  },
  retry: (fileId: string) => json<{ id: string }>(`/files/${fileId}/retry`, { method: "POST" }),
  chunks: (projectId: string, fileId: string) => json<ChunkInfo[]>(`/projects/${projectId}/files/${fileId}/chunks`),
  esInspector: (projectId: string) => json<ESInspector>(`/projects/${projectId}/es-inspector`),
  vectors: (projectId: string, fileId: string, full = false) =>
    json<VectorPeekResult>(`/projects/${projectId}/files/${fileId}/vectors${full ? "?full=true" : ""}`),
  pipeline: (projectId: string, q: string, gate?: number, cutoff?: number) =>
    json<PipelineResult>(
      `/projects/${projectId}/retrieval-pipeline?q=${encodeURIComponent(q)}` +
        `${gate === undefined ? "" : `&gate=${gate}`}${cutoff === undefined ? "" : `&cutoff=${cutoff}`}`,
    ),
  activeIngestion: () => json<ActiveIngestion[]>("/ingestion/active"),
  config: () => json<{ dev_mode: boolean }>("/config"),
  overview: (projectId: string) => json<ProjectOverview>(`/projects/${projectId}/overview`),
  contentUrl: (fileId: string) => `${BASE}/files/${fileId}/content`,
  upload: (projectId: string, items: { file: File; path: string }[]) => {
    const form = new FormData();
    for (const { file, path } of items) {
      form.append("files", file, file.name);
      form.append("paths", path);
    }
    return json<UploadReport>(`/projects/${projectId}/upload`, { method: "POST", body: form });
  },
};
