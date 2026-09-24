/**
 * Feed-frame contract (guest feed-walk path — backend/scraper FeedWalk).
 *
 * One POST /feed-fetch call == one feed frame, returned as RAW bytes. This
 * seam is transport-only: parsing, cursor extraction and normalization all
 * stay in Python (backend/scraper/parser.py). Mirrors the FetchRequest /
 * FetchResponse envelope style of types.ts.
 */

/** FastAPI -> node: fetch one feed frame. */
export interface FeedFrameRequest {
  target_url: string;
  /**
   * Opaque frame cursor (Phase 2: next-frame URL / page token). Absent or
   * null == the first frame of the walk. Accepted for forward-compat; the
   * walker ignores it until the cursor mechanism lands.
   */
  cursor?: string | null;
}

/** node -> FastAPI: one frame, raw bytes only. */
export interface FeedFrameResponse {
  status_code: number;
  final_url: string;
  /** base64 of the raw frame body bytes (HTML or JSON). */
  raw_payload: string;
  /** Crawlee session id that served the frame (null when the pool is off). */
  session_id: string | null;
  /**
   * True when the frame was served but looks like a login/block wall after
   * retries were exhausted — a transport hint only; the backend's
   * classify_page_html stays authoritative.
   */
  blocked: boolean;
  fetched_at_ms: number;
}

/** Internal walker result (raw, before envelope serialization). */
export interface FeedFrame {
  statusCode: number;
  finalUrl: string;
  body: Buffer;
  sessionId: string | null;
  blocked: boolean;
}