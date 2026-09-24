/**
 * Typed API client for the PostHarvest backend.
 *
 * All post text and URLs rendered in the UI must never be trusted: use
 * `safeHttpUrl()` before putting a URL into an href, and let React escape
 * text (we never use dangerouslySetInnerHTML anywhere in this app).
 */
import type {
  AccountSession,
  AccountsResponse,
  CookiesTxtRequest,
  SessionCaptureOut,
  AdminUser,
  ApiErrorBody,
  ExportFormat,
  JobListResponse,
  JobProgress,
  JobStatus,
  PaginatedPosts,
  PersonalLoginRequest,
  PlanCatalog,
  Post,
  ScrapeRequest,
  ScrapeResponse,
  UsageResponse,
  UserProfile,
} from "./types";

import { EXPORT_FILENAMES } from "./types";

/** Resolved at build time. Defaults to the local backend. */
export const API_BASE: string = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

/** Wall-clock ceiling per API call: a wedged backend must surface as an
 * error state, never an infinite spinner. */
export const API_TIMEOUT_MS = 20000;

const TERMINAL_STATUSES: ReadonlySet<string> = new Set(["completed", "failed"]);

function timeoutSignal(ms: number = API_TIMEOUT_MS): AbortSignal {
  // AbortSignal.timeout is widely supported; fall back to manual abort.
  if (typeof AbortSignal.timeout === "function") return AbortSignal.timeout(ms);
  const controller = new AbortController();
  setTimeout(() => controller.abort(), ms);
  return controller.signal;
}

export function isTerminalStatus(status: JobStatus | string | undefined | null): boolean {
  return status != null && TERMINAL_STATUSES.has(status);
}

/** Error with an API error code; thrown for both HTTP error responses and network failures. */
export class ApiError extends Error {
  readonly status?: number;
  readonly code: string;
  readonly details?: unknown;

  constructor(options: { status?: number; code: string; message: string; details?: unknown }) {
    super(options.message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.details = options.details;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
    ...(init?.headers as Record<string, string> ?? {}),
  };

  try {
    const { auth } = await import("./firebase");
    if (auth.currentUser && !headers["Authorization"]) {
      const token = await auth.currentUser.getIdToken();
      if (token) {
        headers["Authorization"] = `Bearer ${token}`;
      }
    }
  } catch {
    // Ignore firebase import errors if running in SSR / build phase
  }

  try {
    response = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      ...init,
      headers,
      signal: init?.signal ?? timeoutSignal(),
    });
  } catch {
    throw new ApiError({
      code: "network_error",
      message: `API unreachable at ${API_BASE}. Is the backend running and is CORS enabled for this origin?`,
    });
  }

  if (!response.ok) {
    let code = "http_error";
    let message = `Request failed with status ${response.status}.`;
    try {
      const body = (await response.json()) as Partial<ApiErrorBody> | null;
      if (body?.error) {
        code = typeof body.error.code === "string" ? body.error.code : code;
        message = typeof body.error.message === "string" ? body.error.message : message;
      }
    } catch {
      // Non-JSON error body — keep the generic message.
    }

    // Stale/expired session → hard redirect to the login page (app routes are
    // gated; the login page itself must never bounce on its own 401s).
    if (
      response.status === 401 &&
      typeof window !== "undefined" &&
      window.location.pathname !== "/login" &&
      ["auth_required", "invalid_token", "account_disabled", "expired_id_token"].includes(code)
    ) {
      window.location.replace("/login");
    }
    throw new ApiError({ status: response.status, code, message });
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** Normalize a raw job payload (backend may omit nullable fields). */
function normalizeJob(raw: Partial<JobProgress>): JobProgress {
  const rawStatus: string = raw?.status ?? "queued";
  const known =
    TERMINAL_STATUSES.has(rawStatus) ||
    rawStatus === "running" ||
    rawStatus === "queued" ||
    rawStatus === "paused";
  const rawSources = Array.isArray(raw?.sources) ? raw.sources : [];
  return {
    job_id: raw?.job_id ?? null,
    status: known ? (rawStatus as JobProgress["status"]) : "queued",
    pages_total: raw?.pages_total ?? null,
    pages_completed: raw?.pages_completed ?? null,
    posts_found: raw?.posts_found ?? null,
    posts_processed: raw?.posts_processed ?? null,
    duplicates: raw?.duplicates ?? null,
    errors: raw?.errors ?? null,
    error_details: raw?.error_details ?? [],
    started_at: raw?.started_at ?? null,
    completed_at: raw?.completed_at ?? null,
    max_posts: raw?.max_posts ?? null,
    sources: rawSources.map((s) => ({
      url: s?.url ?? "",
      status: s?.status ?? "queued",
      posts_found: s?.posts_found ?? 0,
      posts_processed: s?.posts_processed ?? 0,
      error_code: s?.error_code ?? null,
      error_message: s?.error_message ?? null,
    })),
  };
}

function normalizePost(raw: Partial<Post>): Post {
  return {
    post_id: raw?.post_id ?? null,
    facebook_url: raw?.facebook_url ?? null,
    post_url: raw?.post_url ?? null,
    page_name: raw?.page_name ?? null,
    page_id: raw?.page_id ?? null,
    profile_url: raw?.profile_url ?? null,
    post_type: raw?.post_type ?? null,
    published_at: raw?.published_at ?? null,
    timestamp: raw?.timestamp ?? null,
    text: raw?.text ?? null,
    caption: raw?.caption ?? null,
    hashtags: Array.isArray(raw?.hashtags) ? raw.hashtags : [],
    mentions: Array.isArray(raw?.mentions) ? raw.mentions : [],
    external_links: Array.isArray(raw?.external_links) ? raw.external_links : [],
    likes: raw?.likes ?? null,
    reactions: raw?.reactions ?? null,
    comments_count: raw?.comments_count ?? null,
    shares: raw?.shares ?? null,
    views_count: raw?.views_count ?? null,
    reaction_like_count: raw?.reaction_like_count ?? null,
    reaction_love_count: raw?.reaction_love_count ?? null,
    reaction_care_count: raw?.reaction_care_count ?? null,
    reaction_haha_count: raw?.reaction_haha_count ?? null,
    reaction_wow_count: raw?.reaction_wow_count ?? null,
    reaction_sad_count: raw?.reaction_sad_count ?? null,
    reaction_angry_count: raw?.reaction_angry_count ?? null,
    media_type: raw?.media_type ?? null,
    thumbnail_url: raw?.thumbnail_url ?? null,
    media_url: raw?.media_url ?? null,
    video_url: raw?.video_url ?? null,
    transcript: raw?.transcript ?? null,
    transcript_language: raw?.transcript_language ?? null,
  };
}

export const api = {
  /** POST /api/scrape */
  async startScrape(requestBody: ScrapeRequest): Promise<ScrapeResponse> {
    return request<ScrapeResponse>("/api/scrape", {
      method: "POST",
      body: JSON.stringify(requestBody),
    });
  },

  /** GET /api/jobs/{job_id} */
  async getJob(jobId: string): Promise<JobProgress> {
    const raw = await request<Partial<JobProgress>>(`/api/jobs/${encodeURIComponent(jobId)}`);
    return normalizeJob(raw);
  },

  /** GET /api/jobs/{job_id}/posts (paginated) */
  async getPosts(jobId: string, params: { page?: number; page_size?: number } = {}): Promise<PaginatedPosts> {
    const query = new URLSearchParams();
    if (params.page != null) query.set("page", String(params.page));
    if (params.page_size != null) query.set("page_size", String(params.page_size));
    const suffix = query.toString() ? `?${query.toString()}` : "";
    const raw = await request<Partial<PaginatedPosts>>(`/api/jobs/${encodeURIComponent(jobId)}/posts${suffix}`);
    return {
      items: (Array.isArray(raw?.items) ? raw.items : []).map(normalizePost),
      total: raw?.total ?? 0,
      page: raw?.page ?? 1,
      page_size: raw?.page_size ?? params.page_size ?? 0,
    };
  },

  /** DELETE /api/jobs/{job_id} (kept for completeness; the dashboard does not auto-delete) */
  async deleteJob(jobId: string): Promise<void> {
    return request<void>(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
  },

  /** POST /api/jobs/{job_id}/pause */
  async pauseJob(jobId: string): Promise<{ job_id: string; status: string }> {
    return request<{ job_id: string; status: string }>(`/api/jobs/${encodeURIComponent(jobId)}/pause`, {
      method: "POST",
    });
  },

  /** POST /api/jobs/{job_id}/resume */
  async resumeJob(jobId: string): Promise<{ job_id: string; status: string }> {
    return request<{ job_id: string; status: string }>(`/api/jobs/${encodeURIComponent(jobId)}/resume`, {
      method: "POST",
    });
  },

  /**
   * GET /api/jobs/{job_id}/export/{format} — stream a job's results as
   * JSON/CSV/XLSX. Fetches with the Firebase bearer token (headed requests
   * only; a plain navigation carries no auth) and triggers a browser download
   * with the canonical filename. Throws ApiError on failure.
   */
  async exportJobDownload(jobId: string, format: ExportFormat): Promise<void> {
    const url = `${API_BASE}/api/jobs/${encodeURIComponent(jobId)}/export/${format}`;
    const headers: Record<string, string> = { Accept: "application/json" };

    try {
      const { auth } = await import("./firebase");
      if (auth.currentUser) {
        const token = await auth.currentUser.getIdToken();
        if (token) {
          headers["Authorization"] = `Bearer ${token}`;
        }
      }
    } catch {
      // firebase chunks unavailable (build/SSR) — the request below will 401.
    }

    let response: Response;
    try {
      response = await fetch(url, { cache: "no-store", headers, signal: timeoutSignal() });
    } catch {
      throw new ApiError({
        code: "network_error",
        message: `API unreachable at ${API_BASE}. Is the backend running?`,
      });
    }

    if (!response.ok) {
      let code = "http_error";
      let message = `Export failed with status ${response.status}.`;
      try {
        const body = (await response.json()) as Partial<ApiErrorBody> | null;
        if (body?.error) {
          code = typeof body.error.code === "string" ? body.error.code : code;
          message = typeof body.error.message === "string" ? body.error.message : message;
        }
      } catch {
        // Non-JSON error body — keep the generic message.
      }

      // Stale/expired session → same hard redirect the rest of the API uses.
      if (
        response.status === 401 &&
        typeof window !== "undefined" &&
        window.location.pathname !== "/login" &&
        ["auth_required", "invalid_token", "account_disabled", "expired_id_token"].includes(code)
      ) {
        window.location.replace("/login");
      }
      throw new ApiError({ status: response.status, code, message });
    }

    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = EXPORT_FILENAMES[format];
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(objectUrl);
  },

  /** GET /api/jobs — paginated history, newest first. */
  async listJobs(params: { page?: number; page_size?: number; status?: string } = {}): Promise<JobListResponse> {
    const query = new URLSearchParams();
    if (params.page != null) query.set("page", String(params.page));
    if (params.page_size != null) query.set("page_size", String(params.page_size));
    if (params.status != null) query.set("status", params.status);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return request<JobListResponse>(`/api/jobs${suffix}`);
  },

  /** GET /api/accounts — saved sessions split by tier (ops pool + my own). */
  async listAccounts(): Promise<AccountsResponse> {
    return request<AccountsResponse>("/api/accounts");
  },

  /** POST /api/accounts/personal — server-side Facebook login for a personal session. */
  async addPersonalAccount(payload: PersonalLoginRequest): Promise<AccountSession> {
    return request<AccountSession>("/api/accounts/personal", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  /** POST /api/accounts/capture — start a live session capture (returns a same-origin viewer link to open). */
  async startSessionCapture(payload: { name: string; scope: "ops" | "me" }): Promise<SessionCaptureOut> {
    return request<SessionCaptureOut>("/api/accounts/capture", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  /** DELETE /api/accounts/capture/{capture_id} — abort a running capture (best-effort). */
  async cancelSessionCapture(captureId: string): Promise<void> {
    return request<void>(`/api/accounts/capture/${encodeURIComponent(captureId)}`, {
      method: "DELETE",
    });
  },

  /** POST /api/accounts/cookies-txt — add a session by pasting an exported cookies.txt. */
  async addCookiesTxt(payload: CookiesTxtRequest): Promise<AccountSession> {
    return request<AccountSession>("/api/accounts/cookies-txt", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  /** GET /api/auth/me */
  async getProfile(): Promise<UserProfile> {
    return request<UserProfile>("/api/auth/me");
  },

  /** GET /api/plans — canonical tier catalog with server-enforced limits. */
  async listPlans(): Promise<PlanCatalog> {
    return request<PlanCatalog>("/api/plans");
  },

  /** GET /api/usage — used/limit quota readout for the sidebar panel. */
  async getUsage(): Promise<UsageResponse> {
    return request<UsageResponse>("/api/usage");
  },

  /** DELETE /api/accounts/{scope}/{name} — remove a saved session (ops role gates the ops scope). */
  async deleteAccount(scope: string, name: string): Promise<void> {
    return request<void>(`/api/accounts/${encodeURIComponent(scope)}/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
  },

  // --- operator admin (ops role only; backend returns 403 otherwise) ---------

  /** GET /api/admin/users */
  async adminListUsers(): Promise<AdminUser[]> {
    return request<AdminUser[]>("/api/admin/users");
  },

  /** PATCH /api/admin/users/{id}/role */
  async adminSetRole(userId: number, role: string): Promise<AdminUser> {
    return request<AdminUser>(`/api/admin/users/${userId}/role`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    });
  },

  /** PATCH /api/admin/users/{id}/plan */
  async adminSetPlan(userId: number, plan: string): Promise<AdminUser> {
    return request<AdminUser>(`/api/admin/users/${userId}/plan`, {
      method: "PATCH",
      body: JSON.stringify({ plan }),
    });
  },
};

/**
 * Only http(s) URLs survive; everything else (javascript:, data:, control chars) is rejected.
 * Returns null when the input is unsafe.
 */
export function safeHttpUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const trimmed = raw.trim();
  if (!/^https?:\/\//i.test(trimmed)) return null;
  if (/[\u0000-\u001f\u007f]/.test(trimmed)) return null;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    if (!parsed.hostname) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

export interface FacebookUrlCheck {
  valid: boolean;
  normalized: string | null;
  reason: string | null;
}

/**
 * Client-side Facebook URL validation (the backend re-validates authoritatively).
 * Accepts https://(<sub>.)facebook.com/<path> — page, profile and /profile.php?id= forms.
 */
export function isFacebookUrl(raw: string): FacebookUrlCheck {
  const trimmed = raw.trim();
  if (!trimmed) return { valid: false, normalized: null, reason: "Empty URL." };

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return { valid: false, normalized: null, reason: "Not a valid URL." };
  }

  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    return { valid: false, normalized: null, reason: "URL must start with http:// or https://." };
  }

  const host = parsed.hostname.toLowerCase();
  const isFacebookHost = host === "facebook.com" || host.endsWith(".facebook.com");
  if (!isFacebookHost) {
    return { valid: false, normalized: null, reason: `"${host}" is not a facebook.com address.` };
  }

  if (parsed.pathname.length <= 1) {
    return { valid: false, normalized: null, reason: "Provide a page or profile path, e.g. /yourpage." };
  }

  const normalized = parsed.toString().replace(/\/+$/, "");
  return { valid: true, normalized, reason: null };
}