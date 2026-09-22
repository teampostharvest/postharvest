/**
 * REAL-browser integration test for browser-mode capture — no fakes.
 *
 * Launches an actual headless Chromium (via the production `openBrowser`
 * adapter in playwright.ts), navigates to a locally-served Facebook-shaped
 * feed, and drives the real `captureFeed` algorithm end-to-end. It exercises
 * what the hermetic unit tests only simulate:
 *
 *   * real page navigation + the 3s settle + popup dismissal probes
 *   * real in-page evaluate scripts (ROOT_EXTRACT_FN / SCRIPT_EXTRACT_FN)
 *   * real POST /api/graphql/ response interception via page.on("response")
 *   * real final-DOM capture and byte-parity snapshot assembly
 *
 * The only "fixture" here is the local page content (like a golden file) —
 * the browser, the transport, and the algorithm are the production code.
 *
 * Skips cleanly when no Chromium binary can be found (bundled Playwright
 * browser, PLAYWRIGHT_CHROMIUM env, or a system chrome/chromium), so machines
 * without a browser still get a green suite.
 */
import { describe, expect, it, beforeAll, afterAll } from "vitest";
import { createServer, type Server } from "node:http";
import { existsSync, accessSync, constants } from "node:fs";
import { once } from "node:events";
import { captureFeed } from "../src/browser-mode/capture.js";
import { openBrowser } from "../src/browser-mode/playwright.js";
import { HONEST_USER_AGENT } from "../src/config.js";
import type { BrowserOpenOptions } from "../src/browser-mode/types.js";

// A slowly-loaded feed: server-rendered articles, an embedded Relay blob, and
// a late POST /api/graphql/ (the Comet feed pattern) that fires after capture
// has registered its response listener.
const FEED_HTML = `<!doctype html>
<html>
<head><meta charset="utf-8"><title>Feed fixture</title></head>
<body>
  <article id="p1" data-ft='{"tn":"*s"}'>
    <p>First real post body text with plenty of padding to clear the inner-text floor for pooling</p>
  </article>
  <article id="p2">
    <p>Second real post body text with plenty of padding to clear the inner-text floor for pooling</p>
  </article>
  <script id="relay-blob">{"require":[["q",{"__m":"v2","post_id":"999","creation_time":1700000000}]]}</script>
  <script>
    setTimeout(function () {
      fetch("/api/graphql/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ q: "FeedQuery", dep: 1 })
      }).catch(function () {});
    }, 4000);
  </script>
</body>
</html>`;

const GQL_RESPONSE = JSON.stringify({
  data: {
    node: {
      __typename: "Story",
      post_id: "888",
      creation_time: 1700000001,
      message: "pulled live from the real browser Comet feed",
    },
  },
});

// ---------------------------------------------------------------------------
// Chromium discovery — bundled browser first, then env, then system binaries.
// ---------------------------------------------------------------------------

function findChromium(): string | null {
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
  return null;
}

const CHROMIUM = findChromium();

let server: Server | null = null;
let serverUrl = "";

beforeAll(async () => {
  if (!CHROMIUM) return;
  server = createServer((req, res) => {
    if (req.url === "/api/graphql/" && req.method === "POST") {
      res.setHeader("Content-Type", "application/json");
      res.end(GQL_RESPONSE);
      return;
    }
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.end(FEED_HTML);
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("fixture server did not bind a TCP port");
  }
  serverUrl = `http://127.0.0.1:${address.port}`;
});

afterAll(async () => {
  if (server) {
    server.close();
    await once(server, "close");
  }
});

describe.skipIf(!CHROMIUM)("browser-mode real-browser capture", () => {
  it(
    "captures a live feed end-to-end and assembles the byte-parity snapshot",
    async () => {
      const session = await openBrowser(browserOptions());
      try {
        const page = await session.newPage();
        const result = await captureFeed(page, {
          url: `${serverUrl}/feed`,
          scrollRounds: 3,
        });

        // The feed + graphql block both arrived: not a wall, not missing.
        expect(result.stats.loginWall).toBe(false);
        expect(result.stats.feedMissing).toBe(false);
        expect(result.stats.postsFound).toBe(4); // 2 articles + 1 relay blob + 1 graphql

        // Byte-parity skeleton.
        expect(result.html.startsWith("<html><body>")).toBe(true);
        expect(result.html.endsWith("</body></html>")).toBe(true);

        // Real DOM roots pooled from the actual page content.
        expect(result.html).toContain('<article id="p1"');
        expect(result.html).toContain('<article id="p2"');

        // Real script blob pooled from the actual page.
        expect(result.html).toContain('id="relay-blob"');

        // Real graphql response intercepted over the wire, embedded raw.
        expect(result.html).toContain('data-fb-graphql-feed="1"');
        expect(result.html).toContain('"post_id":"888"');

        // Since the Comet feed answered, the missing-marker must be absent.
        expect(result.html).not.toContain("fb-scrape-feed-missing");

        // We came back from the real navigation.
        expect(result.finalUrl).toBe(`${serverUrl}/feed`);
      } finally {
        await session.close();
      }
    },
    120_000,
  );
});

function browserOptions(): BrowserOpenOptions {
  return {
    userAgent: HONEST_USER_AGENT,
    viewport: { width: 1280, height: 900 },
    locale: "en-US",
    launchArgs: ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    blockHeavyResources: true,
    executablePath: CHROMIUM ?? undefined,
  };
}