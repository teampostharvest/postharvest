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
   * Opaque frame cursor (a JSON frame spec, see backend/services/node_feed.py).
   * Absent or null == the first frame of the walk. Passed through verbatim —
   * node never interprets it (parsing stays in Python).
   */
  cursor?: string | null;
  /**
   * HTTP method for this frame. GET (default) fetches a page; POST drives the
   * GraphQL /api/graphql/ pagination frames (form-encoded body below).
   */
  method?: "GET" | "POST";
  /**
   * Form-encoded body fields (POST frames only), e.g. doc_id + the GraphQL
   * variables JSON. Values are always strings.
   */
  form?: Record<string, string>;
  /**
   * Referer header for POST frames — the page being walked (telling the
   * GraphQL endpoint which profile timeline the pagination belongs to).
   */
  referer?: string;
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