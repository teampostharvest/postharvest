/**
 * Fetch error taxonomy.
 *
 * Codes map 1:1 to the unified error envelope (`error.code`) the FastAPI side
 * already uses, so straggler errors stay consistent when FastAPI starts
 * proxying to this service (finalplanv2.md §4, §6).
 */

export type FetchErrorCode =
  | "invalid_url"
  | "robots_disallowed"
  | "rate_limited"
  | "page_unavailable"
  | "timeout"
  | "network_error"
  | "body_too_large";

export class FetchError extends Error {
  readonly code: FetchErrorCode;
  readonly statusCode: number;

  constructor(code: FetchErrorCode, message: string, statusCode?: number) {
    super(message);
    this.name = "FetchError";
    this.code = code;
    this.statusCode = statusCode ?? defaultStatusFor(code);
  }
}

export function defaultStatusFor(code: FetchErrorCode): number {
  switch (code) {
    case "invalid_url":
      return 400;
    case "robots_disallowed":
      return 403;
    case "rate_limited":
      return 429;
    case "body_too_large":
      return 413;
    case "timeout":
      return 504;
    case "page_unavailable":
    case "network_error":
      return 502;
  }
}