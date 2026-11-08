import { apiFetch } from "./http";

export interface Person {
  id: string;
  email: string;
  name: string;
  role: "admin" | "member";
}

export interface Me extends Person {
  previewing: boolean;
}

export interface AuthStatus {
  needs_setup: boolean;
  local_mode: boolean;
}

/** Errors from the sign-in endpoints carry the server's own plain-language message. */
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let message = "Something went wrong. Please try again.";
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      /* keep the generic message */
    }
    throw new Error(message);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const authApi = {
  status: () => call<AuthStatus>("/auth/status"),
  me: () => call<Me>("/auth/me"),
  login: (email: string, password: string) =>
    call<Me>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  setup: (body: { email: string; password: string; name: string }) =>
    call<Me>("/auth/setup", { method: "POST", body: JSON.stringify(body) }),
  logout: () => call<void>("/auth/logout", { method: "POST" }),
  users: () => call<Person[]>("/users"),
  createUser: (body: { email: string; password: string; name: string; role: Person["role"] }) =>
    call<Person>("/users", { method: "POST", body: JSON.stringify(body) }),
  updateUser: (id: string, body: Partial<{ name: string; role: Person["role"]; password: string }>) =>
    call<Person>(`/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteUser: (id: string) => call<void>(`/users/${id}`, { method: "DELETE" }),
};
