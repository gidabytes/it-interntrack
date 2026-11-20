/**
 * Typed fetch wrapper for the IT InternTrack API.
 *
 * Centralises two things that are easy to get wrong in a dozen places:
 * attaching the bearer token, and turning the backend's error shapes into one
 * predictable exception. The API returns `detail` as either a string or an
 * object with `{code, message}`, plus a `problems` array for validation
 * failures, so callers would otherwise each have to unpick that.
 */

const TOKEN_KEY = "interntrack.token";

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly problems?: { field: string; message: string }[];

  constructor(
    status: number,
    message: string,
    code?: string,
    problems?: { field: string; message: string }[],
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.problems = problems;
  }

  /** True when the session is gone and the user must sign in again. */
  get isAuthError() {
    return this.status === 401;
  }

  get isForbidden() {
    return this.status === 403;
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable (private mode); the session lasts the page load */
  }
}

/** Called when any request comes back 401, so the shell can sign the user out. */
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

async function parseError(res: Response): Promise<ApiError> {
  let message = res.statusText || `Request failed (${res.status})`;
  let code: string | undefined;
  let problems: { field: string; message: string }[] | undefined;

  try {
    const body = await res.json();
    if (typeof body?.detail === "string") {
      message = body.detail;
    } else if (body?.detail && typeof body.detail === "object") {
      message = body.detail.message ?? message;
      code = body.detail.code;
      // Schedule conflicts return the findings alongside the message.
      if (Array.isArray(body.detail.errors) && body.detail.errors.length) {
        problems = body.detail.errors.map((e: { code: string; message: string }) => ({
          field: e.code,
          message: e.message,
        }));
      }
    }
    if (Array.isArray(body?.problems)) problems = body.problems;
  } catch {
    /* non-JSON error body; keep the status text */
  }
  return new ApiError(res.status, message, code, problems);
}

type Query = Record<string, string | number | boolean | null | undefined>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.append(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

async function request<T>(
  method: string,
  path: string,
  opts: { body?: unknown; query?: Query } = {},
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(withQuery(path, opts.query), {
    method,
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });

  if (!res.ok) {
    const err = await parseError(res);
    if (err.isAuthError) onUnauthorized?.();
    throw err;
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>("GET", path, { query }),
  post: <T>(path: string, body?: unknown, query?: Query) =>
    request<T>("POST", path, { body, query }),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, { body }),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, { body }),
  del: <T>(path: string) => request<T>("DELETE", path),

  /**
   * Download a generated report. Reports come back as a binary body with a
   * Content-Disposition filename, so this bypasses the JSON path and triggers
   * a browser download.
   */
  async download(path: string, query?: Query, fallbackName = "report") {
    const token = getToken();
    const res = await fetch(withQuery(path, query), {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      const err = await parseError(res);
      if (err.isAuthError) onUnauthorized?.();
      throw err;
    }
    const disposition = res.headers.get("content-disposition") ?? "";
    const match = /filename="?([^";]+)"?/i.exec(disposition);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = match?.[1] ?? fallbackName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};
