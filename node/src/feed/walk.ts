/**
 * Feed-frame walker — Crawlee raw transport for the guest (cookieless) feed
 * path (backend/scraper FeedWalk; the user-validated Apify/Crawlee approach).
 *
 * This module owns the network only. One `fetchFrame()` call == one feed
 * frame, returned as raw bytes; parsing, cursor extraction and normalization
 * stay in Python. It reuses the validated reference mechanics:
 *
 *  - `HttpCrawler` with a persistent `SessionPool` — sessions carry over
 *    across frames of one walk; a blocked frame throws `SessionError`, so
 *    Crawlee discards that session and retries the frame with a fresh one up
 *    to `maxRequestRetries` (the `retryHistogram:[17,1]` behavior from the
 *    reference run).
 *  - single honest fixed Chrome UA + browser-like headers (config.ts) —
 *    never rotated, never spoofed.
 *  - minimal block detection ONLY to drive session rotation; the backend's
 *    `classify_page_html` remains the authoritative verdict.
 *  - no robots.txt enforcement on frame URLs: the feed/pagination endpoints
 *    are robots-restricted, so enforcing robots here would make the walk
 *    impossible (finalplanv2 §4 robots covers the origin page only). The whole
 *    path is behind the backend `GUEST_FEED_WALK` flag, default OFF.
 *  - egress seam: Crawlee's `proxyConfiguration` slot lands here when the
 *    pool is procured; today every session is the same direct IP and session
 *    rotation means a fresh cookie jar + request context.
 */

import { HttpCrawler, SessionError } from "crawlee";
import {
  BROWSER_HEADERS,
  HONEST_USER_AGENT,
  type Config,
} from "../config.js";
import { FetchError } from "../errors.js";
import { waitForToken, type Bucket } from "../fetch/rate-limiter.js";
import { sleep } from "../fetch/util.js";
import { toFacebookUrl } from "../fetch/http-mode.js";
import type { FeedFrame } from "./types.js";

/**
 * High-signal body markers that mean "wall up, rotate session".
 *
 * Tuned empirically: each phrase was verified ABSENT from a genuine 200
 * guest feed frame (~1.4 MB www.facebook.com page HTML) and present on
 * login/checkpoint wall pages. Conservative on purpose — a missed marker
 * only means no rotation (same as today), a false marker wastes retries.
 */
const BLOCK_MARKERS = [
  "unusual traffic",
  "automated access",
  "you have been blocked",
  "temporarily blocked",
  "log into facebook to continue",
];

/** Scan the first 200 KB of a frame for block markers (bounded). */
export function hasBlockMarker(body: string): boolean {
  const low = body.slice(0, 200_000).toLowerCase();
  return BLOCK_MARKERS.some((marker) => low.includes(marker));
}

/**
 * Decide whether a served frame means "rotate the session". 401/403/429 and
 * marker hits rotate; 404/410 are dead ends (no rotation helps); 5xx is a
 * server error Crawlee already retried on its own.
 */
export function looksBlocked(statusCode: number, body: string): boolean {
  if (statusCode === 401 || statusCode === 403 || statusCode === 429) {
    return true;
  }
  if (statusCode >= 500) return false;
  return hasBlockMarker(body);
}

export interface FeedWalker {
  fetchFrame(
    frameUrl: string,
    opts?: FetchFrameOptions,
  ): Promise<FeedFrame>;
  /** Release Crawlee's session pool / request queue resources. */
  close(): Promise<void>;
}

export interface FetchFrameOptions {
  limiter?: Bucket;
  bucketKey?: string;
  delaySeconds?: number;
  timeoutSeconds?: number;
  /** Frame HTTP method (default GET). POST drives GraphQL feed pagination. */
  method?: "GET" | "POST";
  /** Form-encoded body fields for POST frames (always string values). */
  form?: Record<string, string>;
  /** Referer header for POST frames — the page being walked. */
  referer?: string;
}

export interface FeedWalkerDeps {
  config: Config;
}

interface InFlight {
  resolve: (frame: FeedFrame) => void;
  reject: (err: unknown) => void;
}

/**
 * Build the raw Crawlee request descriptor for one frame.
 *
 * Extracted from `fetchFrame` so the POST shape (method, form-encoded payload,
 * GraphQL-appropriate headers) can be unit-tested without driving Crawlee's
 * HTTP stack. GET frames keep the document-navigation browser headers;
 * POST frames switch to cors/same-origin headers (origin: facebook, the page
 * being walked as referer) — the exact shape the live /api/graphql/ probe
 * used.
 */
export function buildFrameRequest(
  frameUrl: string,
  opts: FetchFrameOptions = {},
): {
  url: string;
  method?: "POST";
  headers: Record<string, string>;
  payload?: string;
} {
  const url = toFacebookUrl(frameUrl);
  if (opts.method === "POST") {
    const headers = {
      "User-Agent": HONEST_USER_AGENT,
      Accept: "*/*",
      "Accept-Encoding": "gzip, deflate, br",
      "Accept-Language": "en-US,en;q=0.9",
      "Content-Type": "application/x-www-form-urlencoded",
      Origin: "https://www.facebook.com",
      Referer: opts.referer ?? "https://www.facebook.com/",
      "Sec-Fetch-Dest": "empty",
      "Sec-Fetch-Mode": "cors",
      "Sec-Fetch-Site": "same-origin",
      "Sec-Ch-Ua": '"Chromium";v="128", "Not_A Brand";v="24", "Google Chrome";v="128"',
      "Sec-Ch-Ua-Mobile": "?0",
      "Sec-Ch-Ua-Platform": '"Windows"',
    };
    return {
      url: url.toString(),
      method: "POST",
      headers,
      payload: new URLSearchParams(opts.form ?? {}).toString(),
    };
  }
  return {
    url: url.toString(),
    headers: {
      ...BROWSER_HEADERS,
      "User-Agent": HONEST_USER_AGENT,
    },
  };
}

export function createFeedWalker(deps: FeedWalkerDeps): FeedWalker {
  const config = deps.config;
  const maxRetries = Math.max(config.scraperMaxRetries, 1);
  const timeoutSecs = config.scraperTimeoutSeconds;
  const delaySeconds = config.scraperDelaySeconds;

  let inFlight: InFlight | null = null;
  let lastFrameAt = 0;

  /** Atomically claim the in-flight frame slot, clearing it for the next call. */
  function takeInFlight(): InFlight | null {
    const pending = inFlight;
    inFlight = null;
    return pending;
  }

  const crawler = new HttpCrawler({
    maxRequestRetries: maxRetries,
    requestHandlerTimeoutSecs: timeoutSecs,
    navigationTimeoutSecs: timeoutSecs,
    // Deliver rate-limit / block / dead-end statuses to the handler so we can
    // rotate deliberately instead of letting the client throw on them.
    ignoreHttpErrorStatusCodes: [400, 401, 403, 404, 410, 429],
    useSessionPool: true,
    maxConcurrency: 1,
    // Egress seam: when the proxy pool lands, construct a ProxyConfiguration
    // and pass it here; every session then gets its own egress IP.
    requestHandler: async (context) => {
      const statusCode = context.response.statusCode ?? 0;
      const body =
        typeof context.body === "string"
          ? context.body
          : context.body.toString("utf8");
      const blocked = looksBlocked(statusCode, body);
      if (blocked && context.request.retryCount < maxRetries) {
        // Keep the pending slot claimed; Crawlee will rerun this handler with
        // a fresh session after the SessionError.
        throw new SessionError(
          `blocked frame (HTTP ${statusCode}, retry ${context.request.retryCount + 1})`,
        );
      }
      const pending = takeInFlight();
      if (!pending) return; // no caller waiting (safety)
      pending.resolve({
        statusCode,
        finalUrl: context.response.url ?? context.request.url,
        body: Buffer.isBuffer(context.body)
          ? context.body
          : Buffer.from(body, "utf8"),
        sessionId: context.session?.id ?? null,
        blocked,
      });
    },
    failedRequestHandler: async (_context, error) => {
      const pending = takeInFlight();
      if (!pending) return;
      const message = error instanceof Error ? error.message : String(error);
      if (/timeout|timed out/i.test(message)) {
        pending.reject(
          new FetchError(
            "timeout",
            `feed frame timed out after ${maxRetries} retries: ${message}`,
          ),
        );
        return;
      }
      pending.reject(
        new FetchError(
          "page_unavailable",
          `feed frame failed after ${maxRetries} retries: ${message}`,
        ),
      );
    },
  });

  async function fetchFrame(
    frameUrl: string,
    opts: FetchFrameOptions = {},
  ): Promise<FeedFrame> {
    const url = toFacebookUrl(frameUrl);
    const timeoutMs = (opts.timeoutSeconds ?? timeoutSecs) * 1000;
    const bucketKey = opts.bucketKey ?? `host:${url.hostname}`;

    // Min inter-frame politeness floor (same discipline as http-mode).
    const sinceLast = Date.now() - lastFrameAt;
    if (lastFrameAt > 0 && sinceLast < delaySeconds * 1000) {
      await sleep(delaySeconds * 1000 - sinceLast);
    }
    if (opts.limiter) {
      await waitForToken(opts.limiter, bucketKey, { timeoutMs });
    }
    lastFrameAt = Date.now();

    if (inFlight !== null) {
      throw new FetchError(
        "page_unavailable",
        "feed walker already has an in-flight frame",
      );
    }

    const promise = new Promise<FeedFrame>((resolve, reject) => {
      inFlight = { resolve, reject };
    });
    try {
      const request = buildFrameRequest(frameUrl, opts);
      await crawler.run([request]);
    } catch (err) {
      const pending = takeInFlight();
      if (pending) {
        pending.reject(
          err instanceof FetchError
            ? err
            : new FetchError(
                "page_unavailable",
                `feed walker run failed: ${
                  err instanceof Error ? err.message : String(err)
                }`,
              ),
        );
      }
    }
    // Safety net: if neither handler resolved the frame (missed edge), do not
    // hang the caller.
    const stranded = takeInFlight();
    if (stranded) {
      stranded.reject(
        new FetchError("page_unavailable", "feed walker produced no verdict"),
      );
    }
    return promise;
  }

  async function close(): Promise<void> {
    try {
      await crawler.teardown();
    } catch {
      // best-effort: nothing to release if the crawler never ran
    }
  }

  return { fetchFrame, close };
}