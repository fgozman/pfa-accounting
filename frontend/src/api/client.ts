/**
 * API client.
 *
 * The SPA is a separate origin from the web services: every request goes to
 * ``VITE_API_BASE_URL`` (the Python service), never to the SPA's own origin.
 * There is no dev-server proxy in front of it, so what the browser does in
 * development is exactly what it does in production — including the CORS
 * preflight, which is therefore exercised locally instead of being hidden.
 *
 * * bearer tokens only, `credentials: "omit"` so no cookie is ever sent;
 * * financial values travel as decimal strings and are never parsed into
 *   JavaScript numbers;
 * * one idempotency key is reused for a retry, never regenerated;
 * * downloads use an authenticated fetch followed by a local Blob download, so
 *   files are never exposed through a public object URL.
 */

import { authController } from "../auth/AuthController";
import { tokenMemory } from "../auth/tokens";

export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  code: string;
  correlation_id: string;
  field_errors?: Record<string, string>;
  details?: Record<string, unknown>;
}

export class ApiError extends Error {
  readonly problem: ProblemDetails;
  readonly status: number;

  constructor(problem: ProblemDetails) {
    super(problem.title);
    this.problem = problem;
    this.status = problem.status;
  }

  get title(): string {
    return this.problem.title;
  }

  get fieldErrors(): Record<string, string> {
    return this.problem.field_errors ?? {};
  }

  get missingInputs(): string[] {
    const value = this.problem.details?.missing_inputs;
    return Array.isArray(value) ? (value as string[]) : [];
  }

  /** Ids of the live documents this one duplicates, so the UI can offer to edit them. */
  get duplicateDocumentIds(): string[] {
    const duplicates = this.problem.details?.duplicates;
    if (!Array.isArray(duplicates)) return [];
    return duplicates
      .map((item) => (item as { id?: unknown }).id)
      .filter((id): id is string => typeof id === "string");
  }
}

const IDEMPOTENT_METHODS = new Set(["GET", "HEAD"]);

export function newIdempotencyKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

const env = import.meta.env as Record<string, string | undefined>;

/**
 * Base URL of the web services.  Defaults to the documented local API origin so a
 * misconfigured build fails loudly at the first request instead of silently
 * calling the static host.
 */
export const apiBaseUrl = (env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

/** Absolute URL for an API path (``/api/v1/...``). */
export function apiUrl(path: string, query?: RequestOptions["query"]): string {
  if (!path.startsWith("/")) {
    throw new Error(`API paths must be absolute (got ${path})`);
  }
  const url = new URL(`${apiBaseUrl}${path}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null || value === "") continue;
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  idempotencyKey?: string;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
  /** internal: prevents an infinite refresh loop */
  retried?: boolean;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = {};
  const token = tokenMemory.accessToken;
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.idempotencyKey && !IDEMPOTENT_METHODS.has(method)) {
    headers["Idempotency-Key"] = options.idempotencyKey;
  }

  const response = await fetch(apiUrl(path, options.query), {
    method,
    headers,
    credentials: "omit",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  });

  if (response.status === 401 && !options.retried && tokenMemory.refreshToken) {
    const refreshed = await authController.refresh();
    if (refreshed) {
      // retry once, reusing the original idempotency key
      return apiFetch<T>(path, { ...options, retried: true });
    }
  }

  if (!response.ok) {
    let problem: ProblemDetails;
    try {
      problem = (await response.json()) as ProblemDetails;
    } catch {
      problem = {
        type: "about:blank",
        title: `Eroare ${response.status}`,
        status: response.status,
        code: "http_error",
        correlation_id: response.headers.get("x-correlation-id") ?? "",
      };
    }
    throw new ApiError(problem);
  }

  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) return (await response.json()) as T;
  return (await response.text()) as T;
}

/** Authenticated binary download from the API origin; never a public URL. */
export async function downloadArtifact(path: string, filename: string): Promise<void> {
  const token = tokenMemory.accessToken;
  const response = await fetch(apiUrl(path), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    credentials: "omit",
  });
  if (!response.ok) {
    throw new ApiError({
      type: "about:blank",
      title: "Descărcarea a eșuat.",
      status: response.status,
      code: "download_failed",
      correlation_id: response.headers.get("x-correlation-id") ?? "",
    });
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export interface Paged<T> {
  items: T[];
}
