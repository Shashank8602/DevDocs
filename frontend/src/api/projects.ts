const BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export interface Project {
  id: string;
  name: string;
  description: string | null;
  status: "empty" | "processing" | "ready" | "failed" | "needs_reindex";
  file_count: number;
  created_at: string;
  updated_at: string;
  last_ingested_at: string | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    throw new Error(res.status === 404 ? "Project not found" : "Something went wrong. Please try again.");
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const projectsApi = {
  list: () => request<Project[]>("/projects"),
  get: (id: string) => request<Project>(`/projects/${id}`),
  create: (body: { name: string; description?: string }) =>
    request<Project>("/projects", { method: "POST", body: JSON.stringify(body) }),
  update: (id: string, body: { name: string; description: string }) =>
    request<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  remove: (id: string) => request<void>(`/projects/${id}`, { method: "DELETE" }),
};
