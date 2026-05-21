/**
 * Typed fetch wrapper. All HTTP calls go through here.
 *
 * - Adds `Authorization: Bearer <access>` from the auth store.
 * - Adds `X-Workspace-Id` from the workspace store when set.
 * - Transparent 401 retry via `/api/v1/auth/refresh` (cookie-based).
 */

import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

const PUBLIC_API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

// When running inside Docker, the Next.js server-side runtime cannot reach the backend
// via `localhost` (it would point at the frontend container). Use the internal service
// name for SSR / route handlers / generateStaticParams.
const INTERNAL_API_BASE =
  process.env.SENHARNESS_INTERNAL_API_BASE_URL?.replace(/\/$/, "") || null;

// NEXT_PUBLIC_API_BASE_URL is baked in at build time. When the page is
// served over a LAN IP or alternate hostname, `localhost` in the baked
// value would point the browser at the visitor's own machine. Rewrite
// the host to the current page hostname on the client side (keeping
// the configured protocol and port) so LAN access works without a
// rebuild.
function resolveApiBase(): string {
  if (typeof window === "undefined") {
    return INTERNAL_API_BASE ?? PUBLIC_API_BASE;
  }
  try {
    const parsed = new URL(PUBLIC_API_BASE);
    const isLoopback = parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1";
    const browserHost = window.location.hostname;
    if (isLoopback && browserHost && browserHost !== "localhost" && browserHost !== "127.0.0.1") {
      parsed.hostname = browserHost;
      return parsed.toString().replace(/\/$/, "");
    }
  } catch {
    return PUBLIC_API_BASE;
  }
  return PUBLIC_API_BASE;
}

const API_BASE = resolveApiBase();

export class ApiError extends Error {
  code: string;
  status: number;
  extras: Record<string, unknown>;
  constructor(status: number, code: string, detail: string, extras: Record<string, unknown> = {}) {
    super(detail || code);
    this.status = status;
    this.code = code;
    this.extras = extras;
  }
}

export interface ApiEnvelope {
  code: string;
  detail: string;
  extras?: Record<string, unknown>;
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  skipAuth?: boolean;
  _retryOn401?: boolean;
}

async function buildHeaders(init?: RequestOptions): Promise<Headers> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (init?.body !== undefined && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (!init?.skipAuth) {
    const token = useAuthStore.getState().accessToken;
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const workspaceId = useWorkspaceStore.getState().activeWorkspaceId;
    if (workspaceId) headers.set("X-Workspace-Id", workspaceId);
  }
  return headers;
}

async function parseEnvelope(res: Response): Promise<ApiEnvelope | null> {
  try {
    return (await res.json()) as ApiEnvelope;
  } catch {
    return null;
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  init: RequestOptions = {},
): Promise<T> {
  const url = path.startsWith("http") ? path : `${API_BASE}${path}`;
  const headers = await buildHeaders(init);
  const body =
    init.body === undefined
      ? undefined
      : init.body instanceof FormData
        ? init.body
        : JSON.stringify(init.body);

  const res = await fetch(url, {
    ...init,
    headers,
    body,
    credentials: "include",
  });

  if (res.status === 401 && !init._retryOn401 && !init.skipAuth) {
    // Try refresh once, then retry
    const refreshed = await refreshSilently();
    if (refreshed) {
      return apiFetch<T>(path, { ...init, _retryOn401: true });
    }
    useAuthStore.getState().clear();
  }

  if (!res.ok) {
    const envelope = await parseEnvelope(res);
    throw new ApiError(
      res.status,
      envelope?.code ?? "http.error",
      envelope?.detail ?? res.statusText,
      envelope?.extras ?? {},
    );
  }

  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export async function refreshSilently(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) return false;
    const data = (await res.json()) as { access_token: string; expires_at: string };
    useAuthStore.getState().setAccess(data.access_token, data.expires_at);
    return true;
  } catch {
    return false;
  }
}

// ─── Convenience ──────────────────────────────────────
export const api = {
  get: <T>(path: string, init?: RequestOptions) => apiFetch<T>(path, { ...init, method: "GET" }),
  post: <T>(path: string, body?: unknown, init?: RequestOptions) =>
    apiFetch<T>(path, { ...init, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, init?: RequestOptions) =>
    apiFetch<T>(path, { ...init, method: "PATCH", body }),
  put: <T>(path: string, body?: unknown, init?: RequestOptions) =>
    apiFetch<T>(path, { ...init, method: "PUT", body }),
  delete: <T>(path: string, init?: RequestOptions) =>
    apiFetch<T>(path, { ...init, method: "DELETE" }),
};

export const API_BASE_URL = API_BASE;
