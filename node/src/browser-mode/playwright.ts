/**
 * Real browser glue: opens a headless Chromium session (Playwright) and
 * exposes it through the minimal `CaptureSession` surface the capture
 * algorithm needs. Everything here needs a real browser binary — tests never
 * import this module; they use fakes against `capture.ts` directly.
 *
 * Deployment note: the runtime container must install `playwright-core` plus
 * the chromium binary (`npx playwright install chromium`-equivalent) — or
 * point `executablePath`/`channel` at a system browser. Until one is present,
 * browser-mode requests fail with `browser_launch_failed` at the route
 * (per-source error in FastAPI — never a whole-job failure).
 */

import { chromium, type Browser, type Route } from "playwright-core";
import { existsSync, accessSync, constants } from "node:fs";
import { parseCookieLines, serializeCookies } from "./cookies.js";
import type {
  BrowserOpenOptions,
  CapturePage,
  CaptureSession,
} from "./types.js";

/**
 * Resolve a Chromium binary the way the E2E suite does: honour an explicit
 * `executablePath` first, then env (`PLAYWRIGHT_CHROMIUM` / `CHROME_BIN`),
 * then well-known system locations.  Returns null when only a bundled
 * Playwright browser could work — chromium.launch then falls back to the
 * framework default, which is correct for images that ran
 * `npx playwright install chromium`.
 */
function resolveBrowser(opts: BrowserOpenOptions): string | undefined {
  if (opts.executablePath) return opts.executablePath;
  const candidates = [
    process.env.PLAYWRIGHT_CHROMIUM,
    process.env.CHROME_BIN,
    "/usr/sbin/google-chrome-stable",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ].filter((p): p is string => Boolean(p));
  for (const path of candidates) {
    if (existsSync(path)) {
      try {
        accessSync(path, constants.X_OK);
        return path;
      } catch {
        // not executable — try the next candidate
      }
    }
  }
  return undefined;
}

export async function openBrowser(
  opts: BrowserOpenOptions,
): Promise<CaptureSession> {
  const executablePath = resolveBrowser(opts);
  const browser: Browser = await chromium.launch({
    headless: true,
    args: opts.launchArgs,
    ...(executablePath ? { executablePath } : {}),
    ...(opts.channel ? { channel: opts.channel } : {}),
  });
  const context = await browser.newContext({
    userAgent: opts.userAgent,
    viewport: opts.viewport,
    locale: opts.locale,
  });

  // Apply the saved session (Slice C): parse the RFC 6265 lines the backend
  // serialized and attach them to the context BEFORE any navigation, so the
  // capture opens logged-in (a jar without this is anonymous and hits walls).
  const sessionCookies = parseCookieLines(opts.cookies);
  if (sessionCookies.length > 0) {
    await context.addCookies(
      sessionCookies.map((c) => ({
        name: c.name,
        value: c.value,
        domain: c.domain,
        path: c.path,
        ...(c.expires !== undefined ? { expires: c.expires } : {}),
        ...(c.httpOnly ? { httpOnly: true } : {}),
        ...(c.secure ? { secure: true } : {}),
      })),
    );
  }

  // Block heavy sub-resources — parity with fetch_with_browser's
  // `page.route("**/*", ...)` image/media/font abort (browser_scraper.py:1096).
  if (opts.blockHeavyResources) {
    await context.route("**/*", async (route: Route) => {
      const resourceType = route.request().resourceType();
      if (
        resourceType === "image" ||
        resourceType === "media" ||
        resourceType === "font"
      ) {
        await route.abort();
      } else {
        await route.continue();
      }
    });
  }

  let closed = false;
  const closeOnce = async (): Promise<void> => {
    if (closed) return;
    closed = true;
    await browser.close();
  };

  return {
    newPage: async (): Promise<CapturePage> =>
      (await context.newPage()) as unknown as CapturePage,
    close: closeOnce,
    // Slice C refresh: hand the post-capture session back to FastAPI as RFC
    // 6265 lines on FetchResponse.updated_cookies.  Playwright cookie objects
    // map 1:1 onto the wire format (expires -1 = session, no Expires= attr).
    dumpCookies: async (): Promise<string[]> => {
      const raw = await context.cookies();
      return serializeCookies(
        raw.map((c) => ({
          name: c.name,
          value: c.value,
          domain: c.domain,
          path: c.path,
          ...(c.expires !== undefined && c.expires > 0
            ? { expires: c.expires }
            : {}),
          httpOnly: c.httpOnly,
          secure: c.secure,
        })),
      );
    },
  };
}