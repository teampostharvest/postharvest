/**
 * Phase 1 JSON contract for the fetcher service.
 *
 * Mirrors `shared/proto/postharvest.proto` field-for-field (finalplanv2.md §6):
 * same names, `null` for missing scalars. If you change a shape, update the
 * .proto first, then this file, then the golden fixtures in shared/fixtures/.
 */

/** The canonical 33-key post (backend/scraper/normalizer.py NORMALIZED_KEYS). */
export interface Post {
  post_id: string | null;
  facebook_url: string | null;
  post_url: string | null;
  page_name: string | null;
  page_id: string | null;
  profile_url: string | null;
  post_type: string | null; // "text" | "image" | "video" | "link" | null
  published_at: string | null; // UTC ISO-8601
  timestamp: number | null; // epoch seconds
  text: string | null;
  caption: string | null; // always null for public-HTML scrapes
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
  media_type: string | null; // "video" | "image" | null
  thumbnail_url: string | null;
  media_url: string | null;
  video_url: string | null;
  transcript: string | null;
  transcript_language: string | null;
  scraped_at: string | null; // UTC ISO-8601
}

/** FastAPI -> node-fetcher. */
export interface FetchRequest {
  target_url: string;
  mode: "http" | "browser";
  account_id?: string | null;
}

/** node-fetcher -> FastAPI. `raw_payload` is base64 of the raw body bytes. */
export interface FetchResponse {
  status_code: number;
  final_url: string;
  content_type: string;
  raw_payload: string;
  fetched_at_ms: number;
}

/** FastAPI -> go-worker. */
export interface ParseRequest {
  raw_payload: string; // base64
  content_type: "html" | "graphql_json";
  idempotency_key: string;
}

/** go-worker -> FastAPI. */
export interface ParseResponse {
  posts: Post[];
  errors: string[];
}