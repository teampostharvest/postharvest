/**
 * RFC 6265 cookie-line parsing for the browser-mode seam
 * (finalplanv2.md §7/§12, Slice C prerequisite).
 *
 * FastAPI serializes its Playwright-style jar into
 * `"name=value; Domain=..; Path=..; [Expires=..] [Secure] [HttpOnly]"`
 * lines (backend/services/node_browser.py::serialize_cookies, mirrored by the
 * golden fixture fetch_request.browser.json) and ships them on
 * `FetchRequest.cookies`.  This module parses those lines back into
 * Playwright cookie objects so the capture browser can present the saved
 * session during navigation — node never persists them (DB access stays
 * Python-only, §2/§7).
 */

export interface BrowserCookie {
  name: string;
  value: string;
  /** Cookie domain as serialized (e.g. ".facebook.com"). */
  domain: string;
  path: string;
  /** Epoch seconds. Omitted when the line has no Expires= (session jar). */
  expires?: number;
  httpOnly?: boolean;
  secure?: boolean;
}

/**
 * Parse one cookie line into a Playwright cookie object, or null when the
 * line has no `name=value` pair or no `Domain=` attribute (a bare jar entry
 * cannot be attached to a context).  Unknown attributes (SameSite=, Max-Age=,
 * ...) are ignored; attribute names are case-insensitive per RFC 6265.
 *
 * @param line "c_user=123; Domain=.facebook.com; Path=/; Expires=1758432025; Secure; HttpOnly"
 */
export function parseCookieLine(line: string): BrowserCookie | null {
  if (!line) return null;
  const parts = line.split(";");
  const first = parts[0];
  if (!first) return null;

  const nv = first.trim();
  const eq = nv.indexOf("=");
  if (eq <= 0) return null; // missing name or value
  const name = nv.slice(0, eq).trim();
  const value = nv.slice(eq + 1).trim();
  if (!name) return null;

  const cookie: BrowserCookie = { name, value, domain: "", path: "/" };
  for (const raw of parts.slice(1)) {
    const attr = raw.trim();
    if (!attr) continue;
    const aeq = attr.indexOf("=");
    const key = (aeq === -1 ? attr : attr.slice(0, aeq)).trim().toLowerCase();
    const val = aeq === -1 ? undefined : attr.slice(aeq + 1).trim();
    switch (key) {
      case "domain":
        if (val) cookie.domain = val;
        break;
      case "path":
        if (val) cookie.path = val;
        break;
      case "expires": {
        const ts = Number(val);
        if (Number.isFinite(ts) && ts > 0) cookie.expires = ts;
        break;
      }
      case "secure":
        cookie.secure = true;
        break;
      case "httponly":
        cookie.httpOnly = true;
        break;
      default:
        // SameSite=, Max-Age=, priority, etc. — not needed by Playwright's
        // addCookies and deliberately ignored (parse/normalize stays in
        // FastAPI when a policy ever needs them).
        break;
    }
  }

  if (!cookie.domain || !cookie.name) return null;
  return cookie;
}

/** Parse every line of `FetchRequest.cookies`, dropping malformed entries. */
export function parseCookieLines(lines: string[] | null | undefined): BrowserCookie[] {
  if (!lines) return [];
  const cookies: BrowserCookie[] = [];
  for (const line of lines) {
    const cookie = parseCookieLine(line);
    if (cookie) cookies.push(cookie);
  }
  return cookies;
}

/**
 * Serialize one Playwright-style cookie into the exact RFC 6265 line format
 * FastAPI's `serialize_cookies` produces (mirror of
 * backend/services/node_browser.py::serialize_cookies, golden fixture
 * `fetch_request.browser.json`): `"name=value; Domain=..; Path=..;
 * [Expires=<epoch>] [Secure] [HttpOnly]"`.  Session cookies (expires -1/0 or
 * undefined) omit the `Expires=` attribute; flags appear only when set.
 * Malformed entries (no name/value) are dropped by the caller.
 */
export function serializeCookieLine(cookie: BrowserCookie): string {
  const parts = [`${cookie.name}=${cookie.value}`];
  if (cookie.domain) parts.push(`Domain=${cookie.domain}`);
  if (cookie.path) parts.push(`Path=${cookie.path}`);
  if (typeof cookie.expires === "number" && cookie.expires > 0) {
    parts.push(`Expires=${Math.trunc(cookie.expires)}`);
  }
  if (cookie.secure) parts.push("Secure");
  if (cookie.httpOnly) parts.push("HttpOnly");
  return parts.join("; ");
}

/**
 * Serialize a whole context cookie dump (Slice C refresh): the reverse of
 * `parseCookieLines`, used in playwright.ts to hand the post-capture session
 * back to FastAPI on `FetchResponse.updated_cookies`.  Node never persists
 * them — FastAPI owns the store (§2/§7).
 */
export function serializeCookies(cookies: BrowserCookie[]): string[] {
  const lines: string[] = [];
  for (const cookie of cookies) {
    if (!cookie.name || cookie.value === undefined || cookie.value === null) {
      continue;
    }
    lines.push(serializeCookieLine(cookie));
  }
  return lines;
}