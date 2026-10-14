const BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export interface SettingsValues {
  dev_mode: boolean;
  llm_provider: "groq" | "gemini";
  groq_llm_model: string;
  gemini_llm_model: string;
  groq_fallback_model: string;
  llm_temperature: number;
  llm_max_output_tokens: number;
  llm_reasoning_effort: "low" | "medium" | "high";
  embedding_model: string;
  embedding_dims: number;
  reranker: "cross_encoder" | "jev";
  reranker_model: string;
  rerank_top_n: number;
  jev_base_url: string;
  jev_model: string;
  retrieval_leg_k: number;
  rrf_k: number;
  retrieval_final_top_k: number;
  retrieval_test_path_factor: number;
  retrieval_context_tokens: number;
  gate_vector_threshold: number;
  relative_cutoff: number;
  bm25_protect: number;
}

export type SecretName = "groq_api_key" | "gemini_api_key" | "embedding_api_key" | "jev_api_key";

export interface SettingsView {
  values: SettingsValues;
  defaults: SettingsValues;
  secrets: Record<SecretName, { set: boolean; masked: string }>;
}

export interface SaveResult extends SettingsView {
  changed: string[];
  needs_reindex: number;
}

export type SettingsPatch = Partial<SettingsValues> & Partial<Record<SecretName, string>>;
export type TestTarget = "llm" | "embedding" | "jev";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { ...init, headers: { "Content-Type": "application/json" } });
  if (!res.ok) throw new Error(res.status === 422 ? "One of the values is out of range." : "Something went wrong. Please try again.");
  return res.json();
}

export const settingsApi = {
  get: () => call<SettingsView>("/settings"),
  save: (patch: SettingsPatch) => call<SaveResult>("/settings", { method: "PUT", body: JSON.stringify(patch) }),
  test: (target: TestTarget, values: SettingsPatch) =>
    call<{ ok: boolean; message: string }>("/settings/test", { method: "POST", body: JSON.stringify({ target, ...values }) }),
};

export interface ReindexProgress {
  state: "idle" | "running" | "done" | "error";
  done: number;
  total: number;
  message: string;
}

export const reindexApi = {
  begin: (projectId: string) => call<ReindexProgress>(`/projects/${projectId}/reindex`, { method: "POST" }),
  progress: (projectId: string) => call<ReindexProgress>(`/projects/${projectId}/reindex`),
};
