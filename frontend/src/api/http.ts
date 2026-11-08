// The API defaults to the same host as the page (localhost:5173 -> localhost:8000). The session cookie is
// SameSite=Lax, so it is only sent when both are the same *site*: localhost and 127.0.0.1 are not.
export const BASE = import.meta.env.VITE_API_URL ?? `http://${window.location.hostname}:8000`;

const PREVIEW_KEY = "devdocs.previewAs";

/** "Preview as": an admin sees the whole app as another person (read-only). Kept per browser tab. */
export const previewAs = {
  get: () => sessionStorage.getItem(PREVIEW_KEY),
  set: (userId: string) => sessionStorage.setItem(PREVIEW_KEY, userId),
  clear: () => sessionStorage.removeItem(PREVIEW_KEY),
};

export const UNAUTHORIZED_EVENT = "devdocs:unauthorized";

/**
 * fetch for every backend call: sends the session cookie, adds the preview header, and tells the app when the
 * session is gone (401) so it can show the sign-in screen. Accepts a path ("/projects") or a full URL.
 */
export async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const preview = previewAs.get();
  if (preview) headers.set("X-Preview-As", preview);
  const res = await fetch(input.startsWith("http") ? input : `${BASE}${input}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (res.status === 401 && !input.includes("/auth/")) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  return res;
}
