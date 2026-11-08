import { apiFetch } from "./http";

export type Visibility = "everyone" | "people" | "admin";

export interface AccessRule {
  path: string; // "" = the whole project
  visibility: Visibility;
  user_ids: string[];
}

export interface AccessFile {
  id: string;
  path: string;
  status: string;
}

export interface AccessData {
  rules: AccessRule[];
  files: AccessFile[];
}

export interface PersonFile {
  id: string;
  path: string;
  can_read: boolean;
  reason: string;
}

export interface AuditRow {
  id: string;
  created_at: string;
  user_email: string;
  question: string;
  outcome: "served" | "refused" | "error";
  served_paths: string[];
}

export interface Member {
  id: string;
  email: string;
  name: string;
  role: "admin" | "member";
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (!res.ok) {
    let message = "Something went wrong. Please try again.";
    try {
      const body = await res.json();
      if ((res.status === 409 || res.status === 422) && typeof body.detail === "string") message = body.detail;
    } catch {
      /* keep the generic message */
    }
    throw new Error(message);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const accessApi = {
  get: (projectId: string) => call<AccessData>(`/projects/${projectId}/access`),
  put: (projectId: string, rule: AccessRule) =>
    call<AccessRule>(`/projects/${projectId}/access`, { method: "PUT", body: JSON.stringify(rule) }),
  remove: (projectId: string, path: string) =>
    call<void>(`/projects/${projectId}/access?path=${encodeURIComponent(path)}`, { method: "DELETE" }),
  members: (projectId: string) => call<Member[]>(`/projects/${projectId}/members`),
  addMember: (projectId: string, body: { email: string; name?: string; password?: string }) =>
    call<Member>(`/projects/${projectId}/members`, { method: "POST", body: JSON.stringify(body) }),
  removeMember: (projectId: string, userId: string) =>
    call<void>(`/projects/${projectId}/members/${userId}`, { method: "DELETE" }),
  person: (projectId: string, userId: string) => call<PersonFile[]>(`/projects/${projectId}/access/person/${userId}`),
  audit: (projectId: string) => call<AuditRow[]>(`/projects/${projectId}/audit`),
};
