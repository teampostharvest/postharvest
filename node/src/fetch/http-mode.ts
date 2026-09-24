/**
 * HTTP-mode fetcher — the only place this service makes real network calls
 * to Facebook (finalplanv2.md §4).
 *
 * Invariants mirrored from backend/scraper/:
 *  - Single honest, fixed Chrome UA (config.HONEST_USER_AGENT).
 *  - robots.txt respected before fetching (allowlist-only on failure).
 *  - One token-bucket token per request, keyed per account/host.
 *  - Min inter-request delay (SCRAPER_DELAY_SECONDS), exponential backoff with
 *    jitter on 429/5xx, capped at 30 s, honoring Retry-After.
 *  - 10 MB body safety cap. No cookies are ever persisted.
 */

import { request } from "undici";
import {
  brotliDecompressSync,
  gunzipSync,
  inflateSync,
} from "node:zlib";
import {
  BROWSER_HEADERS,
  loadConfig as _loadConfig,
  type Config,
} from "../config.js";
import { FetchError } from "../errors.js";
import {
  backoff,
  classifyNetworkError,
  parseRetryAfter,
  readBody,
  sleep,
} from "./util.js";
import type { RobotsPolicy } from "./robots.js";
import type { Bucket } from "./rate-limiter.js";
import { waitForToken } from "./rate-limiter.js";

export interface FetchResult {
  statusCode: number;
  finalUrl: string;
  contentType: string;
  /** Decompressed raw body bytes (content-encoding already applied). */
  body: Buffer;
  bodyBytes: number;
  fetchedAtMs: number;
}

export interface TransportResponse {
  statusCode: number;
  headers: Record<string, string>;
  body: AsyncIterable<Uint8Array>;
}

/** Lowest-level HTTP seam; the real one is undici, tests stub it. */
export type FetchTransport = (
  url: string,
  init: {
    method: "GET";
    headers: Readonly<Record<string, string>>;
    headersTimeout: number;
    bodyTimeout: number;
  },
) => Promise<TransportResponse>;

export interface FetchPageOptions {
  config?: Config;
  limiter?: Bucket;
  robots?: RobotsPolicy;
  transport?: FetchTransport;
  /** Rate-limit bucket key; defaults to the hostname. */
  bucketKey?: string;
  delaySeconds?: number;
  timeoutMs?: number;
  timeoutTotalMs?: number;
  maxRetries?: number;
  maxBodyBytes?: number;
  now?: () => number;
}

export async function fetchPage(
  targetUrl: string,
  opts: FetchPageOptions = {},
): Promise<FetchResult> {
  const config = opts.config ?? _loadConfig();
  const timeoutMs = opts.timeoutMs ?? config.scraperTimeoutSeconds * 1000;
  const maxRetries = opts.maxRetries ?? config.scraperMaxRetries;
  const maxRedirections = 5;
  const delaySeconds = opts.delaySeconds ?? config.scraperDelaySeconds;
  const maxBodyBytes = opts.maxBodyBytes ?? config.maxBodyBytes;
  const now = opts.now ?? Date.now;
  const timeoutTotalMs = opts.timeoutTotalMs ?? 90_000;

  const url = toFacebookUrl(targetUrl);

  if (opts.robots) {
    const decision = await opts.robots.canFetch(url.toString());
    if (!decision.allowed) {
      throw new FetchError(
        "robots_disallowed",
        `robots.txt: ${decision.reason ?? "disallowed"}`,
      );
    }
  }

  const bucketKey = opts.bucketKey ?? `host:${url.hostname}`;
  const started = now();

  let lastRequestAt: number | null = null;
  for (let attempt = 1; ; attempt++) {
    if (now() - started > timeoutTotalMs) {
      throw new FetchError(
        "timeout",
        `Fetch timed out after ${timeoutTotalMs} ms total`,
      );
    }

    if (lastRequestAt !== null) {
      const wait = delaySeconds * 1000 - (now() - lastRequestAt);
      if (wait > 0) await sleep(wait);
    }

    if (opts.limiter) {
      await waitForToken(opts.limiter, bucketKey, { timeoutMs });
    }
    lastRequestAt = now();

    let response: TransportResponse;
    let finalUrl: string;
    try {
      const transport = opts.transport ?? undiciTransport;
      ({ response, finalUrl } = await followRedirects(
        transport,
        url.toString(),
        maxRedirections,
        delaySeconds,
        timeoutMs,
      ));
    } catch (err) {
      if (attempt <= maxRetries) {
        await backoff(attempt - 1, delaySeconds);
        continue;
      }
      throw classifyNetworkError(err);
    }

    const { statusCode, headers, body } = response;
    if (statusCode === 429 || statusCode >= 500) {
      const retryAfter = parseRetryAfter(headers["retry-after"]);
      if (attempt <= maxRetries) {
        await backoff(attempt - 1, delaySeconds, retryAfter);
        continue;
      }
      throw new FetchError(
        statusCode === 429 ? "rate_limited" : "page_unavailable",
        `Facebook returned HTTP ${statusCode} after ${attempt} attempts`,
      );
    }

    const buf = await readBody(body, maxBodyBytes);
    const contentType = headers["content-type"] ?? "text/html";
    const decoded = decodeContentEncoding(buf, headers["content-encoding"], maxBodyBytes);

    return {
      statusCode,
      finalUrl,
      contentType,
      body: decoded,
      bodyBytes: decoded.length,
      fetchedAtMs: Date.now(),
    };
  }
}

/**
 * Apply content-encoding (gzip/deflate/brotli) — parity with Python's httpx
 * auto-decode. Bounded by `maxBytes` via zlib's `maxOutputLength` so a
 * decompression bomb cannot blow past the safety cap. On an undecodable or
 * oversized payload, pass the raw bytes through (some CDNs lie).
 */
function decodeContentEncoding(
  buf: Buffer,
  contentEncoding: string | undefined,
  maxBytes: number,
): Buffer {
  const enc = (contentEncoding ?? "").toLowerCase().trim();
  if (enc === "" || enc === "identity") return buf;
  try {
    const opts = { maxOutputLength: maxBytes };
    if (enc === "gzip") return gunzipSync(buf, opts);
    if (enc === "deflate") return inflateSync(buf, opts);
    if (enc === "br") return brotliDecompressSync(buf, opts);
    return buf; // unknown encoding — pass through
  } catch {
    return buf;
  }
}

/**
 * Follow 3xx redirects up to `maxRedirections` hops, resolving `Location`
 * against the current URL. Redirect hops honor the min inter-request delay
 * but do not consume additional rate-limit tokens (same logical fetch).
 */
async function followRedirects(
  transport: FetchTransport,
  startUrl: string,
  maxRedirections: number,
  delaySeconds: number,
  timeoutMs: number,
): Promise<{ response: TransportResponse; finalUrl: string }> {
  let url = startUrl;
  for (let hop = 0; ; hop++) {
    const response = await transport(url, {
      method: "GET",
      headers: BROWSER_HEADERS,
      headersTimeout: timeoutMs,
      bodyTimeout: timeoutMs,
    });
    const status = response.statusCode;
    const location = response.headers.location;
    const isRedirect =
      status >= 300 && status < 400 && location !== undefined && location !== "";
    if (!isRedirect || hop >= maxRedirections) {
      return { response, finalUrl: url };
    }
    if (delaySeconds > 0) await sleep(delaySeconds * 1000);
    url = new URL(location, url).toString();
  }
}

function undiciTransport(
  url: string,
  init: Parameters<FetchTransport>[1],
): Promise<TransportResponse> {
  return request(url, init).then((res) => ({
    statusCode: res.statusCode,
    headers: res.headers as Record<string, string>,
    body: res.body as unknown as AsyncIterable<Uint8Array>,
  }));
}

export function toFacebookUrl(targetUrl: string): URL {
  let url: URL;
  try {
    url = new URL(targetUrl);
  } catch {
    throw new FetchError("invalid_url", `Malformed URL: ${targetUrl}`);
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    throw new FetchError("invalid_url", `Unsupported protocol: ${url.protocol}`);
  }
  const host = url.hostname.toLowerCase();
  if (host !== "www.facebook.com" && !host.endsWith(".facebook.com")) {
    throw new FetchError(
      "invalid_url",
      `Only facebook.com targets are allowed (got ${host})`,
    );
  }
  return url;
}