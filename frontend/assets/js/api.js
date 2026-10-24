// API client: attaches the session token, refreshes it once when it expires, and turns every
// error response into an ApiError with a message that is safe to show.
import { API_BASE } from "./config.js";

const KEY = "shortlist.session";

export class ApiError extends Error {
  constructor(status, code, message, fields = []) {
    super(message);
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

export const session = {
  get() {
    try { return JSON.parse(localStorage.getItem(KEY)) || null; } catch { return null; }
  },
  set(s) {
    localStorage.setItem(KEY, JSON.stringify({ ...s, expires_at: Date.now() + (s.expires_in || 3600) * 1000 }));
  },
  clear() { localStorage.removeItem(KEY); },
};

let refreshing = null;

async function refresh() {
  const s = session.get();
  if (!s?.refresh_token) throw new ApiError(401, "session_expired", "Your session has expired. Sign in again.");
  refreshing ??= raw("POST", "/api/auth/refresh", { body: { refresh_token: s.refresh_token } })
    .then((next) => { session.set(next); return next; })
    .finally(() => { refreshing = null; });
  return refreshing;
}

async function raw(method, path, { body, form, token, query, signal } = {}) {
  const url = new URL(API_BASE + path);
  for (const [k, v] of Object.entries(query || {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  }
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }

  let res;
  try {
    res = await fetch(url, { method, headers, body: payload, signal, credentials: "omit", referrerPolicy: "no-referrer" });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError(0, "network", "Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error */ }
  if (!res.ok) {
    const err = data?.error || {};
    throw new ApiError(res.status, err.code || "error", err.message || `Request failed (${res.status})`, err.fields || []);
  }
  return data;
}

export async function api(method, path, opts = {}) {
  let s = session.get();
  if (s && s.expires_at && s.expires_at - Date.now() < 30_000) {
    try { s = await refresh(); } catch { /* fall through; the request will 401 */ }
  }
  try {
    return await raw(method, path, { ...opts, token: s?.access_token });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401 && e.code === "token_expired" && s?.refresh_token) {
      const next = await refresh();
      return raw(method, path, { ...opts, token: next.access_token });
    }
    if (e instanceof ApiError && e.status === 401) {
      session.clear();
      const portal = window.location.pathname.includes("/recruiter/") ? "recruiter" : "applicant";
      window.location.replace(`/${portal}/login.html?expired=1`);
    }
    throw e;
  }
}

export const get = (path, query) => api("GET", path, { query });
export const post = (path, body) => api("POST", path, { body });
export const patch = (path, body) => api("PATCH", path, { body });
export const put = (path, body) => api("PUT", path, { body });
export const del = (path) => api("DELETE", path);
export const upload = (path, formData) => api("POST", path, { form: formData });
export const publicPost = (path, body) => raw("POST", path, { body });

export async function signOut() {
  const s = session.get();
  session.clear();
  if (s?.access_token) {
    try { await raw("POST", "/api/auth/logout", { token: s.access_token }); } catch { /* already signed out */ }
  }
}
