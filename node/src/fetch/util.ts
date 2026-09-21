/** Shared stream/sleep/backoff helpers, re-used by http-mode and robots. */

import { FetchError } from "../errors.js";

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Drain a body stream, failing with `body_too_large` past `maxBytes`. */
export async function readBody(
  stream: AsyncIterable<Uint8Array>,
  maxBytes: number,
): Promise<Buffer> {
  const chunks: Uint8Array[] = [];
  let total = 0;
  for await (const chunk of stream) {
    total += chunk.byteLength;
    if (total > maxBytes) {
      throw new FetchError(
        "body_too_large",
        `Response body exceeded the ${maxBytes}-byte safety cap`,
      );
    }
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

export function parseRetryAfter(value: unknown): number | undefined {
  if (typeof value !== "string" || value.trim() === "") return undefined;
  const n = Number(value.trim());
  return Number.isFinite(n) ? Math.max(0, n) : undefined;
}

/** Exponential backoff with jitter (0.8–1.2x), capped at 30 s, honoring Retry-After. */
export async function backoff(
  attempt: number,
  delaySeconds: number,
  retryAfter?: number,
): Promise<void> {
  const jitter = 0.8 + Math.random() * 0.4;
  const base = Math.min(delaySeconds * Math.pow(2, attempt) * jitter, 30);
  const wait = retryAfter !== undefined ? Math.max(base, retryAfter) : base;
  await sleep(wait * 1000);
}

/** Classify an undici network error into our taxonomy. */
export function classifyNetworkError(err: unknown): FetchError {
  const anyErr = err as { name?: string; code?: string; message?: string };
  const raw = [
    anyErr?.code ?? "",
    anyErr?.name ?? "",
    anyErr?.message ?? "",
  ].join(" ");
  if (/timeout|UND_ERR_(HEADERS|BODY|CONNECT|SOCKET)_TIMEOUT/i.test(raw)) {
    return new FetchError("timeout", `Request timed out: ${anyErr?.message ?? raw}`);
  }
  return new FetchError(
    "network_error",
    `Network failure: ${anyErr?.message ?? "unknown error"}`,
  );
}