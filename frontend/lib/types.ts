/**
 * Shared frontend types — mirrors the backend API DTOs (see plan.md canonical contracts).
 * Every field on Post is nullable (or an array) because extrated data may be incomplete;
 * missing values are never fabricated by the backend.
 */

export type PostType = "text" | "image" | "video" | "link";

export type JobStatus = "queued" | "running" | "completed" | "failed" | "paused";

export type ExportFormat = "json" | "csv" | "excel";

/** POST /api/scrape request body */
export interface ScrapeRequest {
  urls: string[];
  max_posts?: number | null;
  start_date?: string | null;
  end_date?: string | null;
  post_type?: PostType | null;
  use_browser?: boolean;
  account?: string | null;
  scrolls?: number | null;
}

/** POST /api/scrape response */
export interface ScrapeResponse {
  job_id: string;
  status: JobStatus;
}

/** One entry in GET /api/jobs/{id} -> error_details */
export interface JobErrorDetail {
  url?: string | null;
  post_url?: string | null;
  code: string;
  message: string;
}

/** One entry in GET /api/jobs/{id} -> sources (links being scraped) */
export interface SourceProgress {
  url: string;
  status: string;
  posts_found: number;
  posts_processed: number;
  error_code?: string | null;
  error_message?: string | null;
}

/** GET /api/jobs/{id} response (fields may be null until the job reports them) */
export interface JobProgress {
  job_id?: string | null;
  status: JobStatus;
  pages_total: number | null;
  pages_completed: number | null;
  posts_found: number | null;
  posts_processed: number | null;
  duplicates: number | null;
  errors: number | null;
  error_details?: JobErrorDetail[] | null;
  started_at?: string | null;
  completed_at?: string | null;
  max_posts?: number | null;
  sources?: SourceProgress[] | null;
}

/** Normalized Facebook post (single source of truth from plan.md) */
export interface Post {
  post_id: string | null;
  facebook_url: string | null;
  post_url: string | null;
  page_name: string | null;
  page_id: string | null;
  profile_url: string | null;
  post_type: PostType | null;
  published_at: string | null;
  timestamp: number | null;

  text: string | null;
  caption: string | null;
  hashtags: string[];
  mentions: string[];
  external_links: string[];

  likes: number | null;
  reactions: number | null;
  comments_count: number | null;
  shares: number | null;
  views_count: number | null;

  reaction_like_count: number | null;
  reaction_love_count: number | null;
  reaction_care_count: number | null;
  reaction_haha_count: number | null;
  reaction_wow_count: number | null;
  reaction_sad_count: number | null;
  reaction_angry_count: number | null;

  media_type: string | null;
  thumbnail_url: string | null;
  media_url: string | null;
  video_url: string | null;
  transcript: string | null;
  transcript_language: string | null;
}

/** GET /api/jobs/{id}/posts response (paginated) */
export interface PaginatedPosts {
  items: Post[];
  total: number;
  page: number;
  page_size: number;
}

/** One row in GET /api/jobs (history) */
export interface JobSummary {
  job_id: string;
  status: JobStatus;
  pages_total: number;
  pages_completed: number;
  posts_found: number;
  posts_processed: number;
  duplicates: number;
  errors: number;
  urls: string[];
  max_posts?: number | null;
  post_type?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
}

/** GET /api/jobs response (paginated history) */
export interface JobListResponse {
  items: JobSummary[];
  total: number;
  page: number;
  page_size: number;
}

/** Metadata row in GET /api/accounts (namespaced ops pool or personal). */
export interface AccountSession {
  name: string;
  scope: "ops" | "me";
  cookies_file?: string | null;
  saved_at?: string | null;
  status?: "VALID" | "EXPIRED" | null;
}

/** GET /api/accounts response — ops pool + your own sessions. */
export interface AccountsResponse {
  ops: AccountSession[];
  mine: AccountSession[];
}

/** Request body for POST /api/accounts/personal (server-side Facebook login). */
export interface PersonalLoginRequest {
  name: string;
  email: string;
  password: string;
}

/** Request body for POST /api/accounts/cookies-txt (paste an exported jar). */
export interface CookiesTxtRequest {
  name: string;
  scope: "ops" | "me";
  cookies_txt: string;
}

/** Response from POST /api/accounts/capture — the same-origin viewer link to open. */
export interface SessionCaptureOut {
  capture_id: string;
  name: string;
  scope: "ops" | "me";
  url: string;
  expires_at: string;
}

/** One row in GET /api/admin/users (ops role only). */
export interface AdminUser {
  id: number;
  email: string | null;
  display_name: string | null;
  plan: string;
  role: string;
  is_active: boolean;
  created_at: string | null;
}

export const PLAN_LABELS: Record<string, string> = {
  basic: "Basic",
  pro: "Pro",
  team: "Team",
  enterprise: "Enterprise",
};

export type PlanName = "basic" | "pro" | "team" | "enterprise";

export const PLAN_IDS: PlanName[] = ["basic", "pro", "team", "enterprise"];

/** Server-enforced limits for a tier (`null` = no per-plan ceiling). */
export interface PlanLimits {
  urls: number | null;
  max_posts: number | null;
  concurrent_jobs: number | null;
  personal_accounts: number | null;
}

/** One entry from GET /api/plans (mirrors backend/core/plans.py). */
export interface PlanCatalogEntry {
  id: PlanName;
  name: string;
  limits: PlanLimits;
}

export type PlanCatalog = PlanCatalogEntry[];

/** One used/limit pair from GET /api/usage (`null` limit = no ceiling). */
export interface UsageLimit {
  used: number;
  limit: number | null;
}

/** Quota readout from GET /api/usage (mirrors backend/api/usage.py). */
export interface UsageResponse {
  plan: string;
  jobs_running: UsageLimit;
  personal_accounts: UsageLimit;
  per_job: {
    urls: number | null;
    max_posts: number | null;
  };
  posts_today: number;
  jobs_today: number;
}

/** User profile returned by GET /api/auth/me */
export interface UserProfile {
  id: number;
  firebase_uid: string;
  email: string;
  display_name: string | null;
  photo_url: string | null;
  is_active: boolean;
  plan: string;
  role: string;
  created_at: string | null;
  updated_at: string | null;
}

/** Standard error body: {"error": {"code": "...", "message": "..."}} */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
  };
}

export const POST_TYPE_LABELS: Record<string, string> = {
  text: "Text",
  image: "Image",
  video: "Video",
  link: "Link",
};

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  paused: "Paused",
};

export const EXPORT_FILENAMES: Record<ExportFormat, string> = {
  json: "facebook_posts.json",
  csv: "facebook_posts.csv",
  excel: "facebook_posts.xlsx",
};