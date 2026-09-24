import { describe, expect, it, vi } from "vitest";
import { buildApp } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import { FetchError } from "../src/errors.js";
import type { FetchResult } from "../src/fetch/http-mode.js";
import type { RobotsDecision, RobotsPolicy } from "../src/fetch/robots.js";
import type { BuiltApp } from "../src/server.js";
import type {
  BrowserOpenFn,
  CapturePage,
  CaptureSession,
} from "../src/browser-mode/types.js";

const config = loadConfig({ REDIS_URL: "", SCRAPER_ROBOTS: "0" });

const FB_URL = "https://www.facebook.com/NASA";

function okResult(overrides: Partial<FetchResult> = {}): FetchResult {
  return {
    statusCode: 200,
    finalUrl: FB_URL,
    contentType: "text/html",
    body: Buffer.from("<html><body>hello</body></html>", "utf8"),
    bodyBytes: 30,
    fetchedAtMs: 1_758_432_000_000,
    ...overrides,
  };
}

function allowAll(): RobotsPolicy {
  return {
    canFetch: vi.fn(async (): Promise<RobotsDecision> => ({ allowed: true })),
  };
}

function denyAll(): RobotsPolicy {
  return {
    canFetch: vi.fn(
      async (): Promise<RobotsDecision> => ({
        allowed: false,
        reason: "disallowed by test",
      }),
    ),
  };
}

async function withApp(
  overrides: Partial<Parameters<typeof buildApp>[0]> = {},
): Promise<BuiltApp> {
  const built = buildApp({
    config,
    redis: null,
    robots: allowAll(),
    fetchImpl: vi.fn(async () => okResult()),
    ...overrides,
  });
  return built;
}

/**
 * A page whose inspectable in-page scripts are dispatched by source and return
 * deterministic "empty page" values, so the real capture loop runs hermetically
 * without a browser: no posts, no graphql bodies, no login wall.
 */
function stubPage(finalDom = "<div>final</div>"): CapturePage {
  return {
    goto: vi.fn(async () => {}),
    waitForTimeout: vi.fn(async () => {}),
    url: vi.fn(() => FB_URL),
    content: vi.fn(async () => finalDom),
    evaluate: (async (fn: unknown) => {
      const src = String(fn);
      if (src.includes("window.scrollTo")) return undefined;
      if (src.includes('input[name="email"]')) {
        return { url: FB_URL, hasLoginForm: false, isLoginPage: false };
      }
      if (src.includes('[role="tab"]')) return false;
      if (src.includes("data-ad-preview")) {
        return [] as Array<{ html: string; text: string; aria: string }>;
      }
      if (src.includes('querySelectorAll("script")')) return [] as string[];
      return undefined;
    }) as CapturePage["evaluate"],
    on: vi.fn(),
    querySelector: vi.fn(async () => null),
    click: vi.fn(async () => {}),
  } as unknown as CapturePage;
}

function fakeBrowser(
  session?: Partial<CaptureSession>,
): BrowserOpenFn {
  return vi.fn(async () => ({
    newPage: vi.fn(async () => stubPage()),
    close: vi.fn(async () => {}),
    ...session,
  }));
}

describe("POST /fetch", () => {
  it("rejects a missing target_url", async () => {
    const { app } = await withApp();
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: {},
    });
    expect(res.statusCode).toBe(400);
    expect(res.json()).toEqual({
      error: { code: "invalid_url", message: "target_url is required" },
    });
    await app.close();
  });

  it("rejects malformed URLs", async () => {
    const { app } = await withApp();
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: "not a url" },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.code).toBe("invalid_url");
    await app.close();
  });

  it("rejects non-facebook targets", async () => {
    const { app } = await withApp();
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: "https://example.com" },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.code).toBe("invalid_url");
    await app.close();
  });

  it("rejects an unknown mode", async () => {
    const { app } = await withApp();
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: FB_URL, mode: "teleport" },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.code).toBe("invalid_mode");
    await app.close();
  });

  it("browser mode returns a captured snapshot with canonical stats", async () => {
    const { app } = await withApp({ browserOpen: fakeBrowser() });
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: FB_URL, mode: "browser" },
    });
    expect(res.statusCode).toBe(200);
    const json = res.json();
    expect(json.content_type).toBe("text/html");
    // Canonical FetchResponse shape (§6): repeated -> [] until cookie support.
    expect(json.updated_cookies).toEqual([]);
    expect(json.browser_stats).toEqual({
      login_wall: false,
      feed_missing: true, // no /api/graphql/ bodies in this fake capture
      posts_found: 0,
    });
    // The fake capture ran the whole loop against an empty page.
    expect(Buffer.from(json.raw_payload, "base64").toString("utf8")).toBe(
      "<html><body><!-- fb-scrape-feed-missing --><div>final</div></body></html>",
    );
    await app.close();
  });

  it("browser mode threads a dumpCookies result onto updated_cookies (Slice C refresh)", async () => {
    // golden ``updated_cookies: []`` when the session has no dump and nodes
    // that implement it (Slice C refresh) thread them through.  This test
    // proves the positive path: whatever ``session.dumpCookies()`` emits rides
    // onto ``FetchResponse.updated_cookies`` verbatim — FastAPI then persists
    // them (slice C write-back; hermetic, node never persists).
    const session = {
      dumpCookies: vi.fn(async () => [
        "xs=newtoken456def; Domain=.facebook.com; Path=/; Secure; HttpOnly",
      ]),
    };
    const browserOpen = fakeBrowser(session);
    const { app } = await withApp({ browserOpen });
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: FB_URL, mode: "browser" },
    });
    expect(res.statusCode).toBe(200);
    expect(res.json().updated_cookies).toEqual([
      "xs=newtoken456def; Domain=.facebook.com; Path=/; Secure; HttpOnly",
    ]);
    await app.close();
  });

  it("browser mode forwards session cookies to browserOpen (Slice C)", async () => {
    const browserOpen = fakeBrowser();
    const { app } = await withApp({ browserOpen });
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: {
        target_url: FB_URL,
        mode: "browser",
        cookies: [
          "c_user=100000000000001; Domain=.facebook.com; Path=/; HttpOnly",
          "xs=abc123def456; Domain=.facebook.com; Path=/; Secure; HttpOnly",
        ],
      },
    });
    expect(res.statusCode).toBe(200);
    expect(browserOpen).toHaveBeenCalledWith(
      expect.objectContaining({
        cookies: [
          "c_user=100000000000001; Domain=.facebook.com; Path=/; HttpOnly",
          "xs=abc123def456; Domain=.facebook.com; Path=/; Secure; HttpOnly",
        ],
      }),
    );
    await app.close();
  });

  it("browser mode maps a launch failure to 500 browser_launch_failed", async () => {
    const browserOpen: BrowserOpenFn = vi.fn(async () => {
      throw new Error("chromium missing");
    });
    const { app } = await withApp({ browserOpen });
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: FB_URL, mode: "browser" },
    });
    expect(res.statusCode).toBe(500);
    expect(res.json().error.code).toBe("browser_launch_failed");
    await app.close();
  });

  it("browser mode maps a page failure to 500 browser_capture_failed", async () => {
    const browserOpen: BrowserOpenFn = vi.fn(async () => ({
      newPage: vi.fn(async () => {
        throw new Error("page crashed");
      }),
      close: vi.fn(async () => {}),
    }));
    const { app } = await withApp({ browserOpen });
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: FB_URL, mode: "browser" },
    });
    expect(res.statusCode).toBe(500);
    expect(res.json().error.code).toBe("browser_capture_failed");
    await app.close();
  });

  it("refuses when robots.txt disallows, before any fetch", async () => {
    // The route passes robots through to the fetcher; use a fetcher that
    // consults it (the same gate the real fetchPage applies) and assert the
    // route maps robots_disallowed -> 403 without ever touching the transport.
    const fetchImpl = vi.fn(
      async (
        _url: string,
        opts: { robots?: RobotsPolicy },
      ): Promise<FetchResult> => {
        if (opts.robots) {
          const decision = await opts.robots.canFetch(_url);
          if (!decision.allowed) {
            throw new FetchError("robots_disallowed", decision.reason ?? "disallowed");
          }
        }
        return okResult();
      },
    );
    const { app } = await withApp({ robots: denyAll(), fetchImpl });
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: FB_URL },
    });
    expect(res.statusCode).toBe(403);
    expect(res.json().error.code).toBe("robots_disallowed");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    await app.close();
  });

  it("returns a base64 FetchResponse on success", async () => {
    const { app } = await withApp();
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: FB_URL, account_id: "acct_1" },
    });
    expect(res.statusCode).toBe(200);
    const json = res.json();
    expect(json.status_code).toBe(200);
    expect(json.final_url).toBe(FB_URL);
    expect(json.content_type).toBe("text/html");
    expect(Buffer.from(json.raw_payload, "base64").toString("utf8")).toBe(
      "<html><body>hello</body></html>",
    );
    expect(json.fetched_at_ms).toBe(1_758_432_000_000);
    // Canonical FetchResponse shape (proto3 JSON defaults, §6): http-mode
    // emits repeated -> [] and message -> null.
    expect(json.updated_cookies).toEqual([]);
    expect(json.browser_stats).toBeNull();
    await app.close();
  });

  it("maps rate_limited to 429 with the error envelope", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new FetchError("rate_limited", "facebook throttled us");
    });
    const { app } = await withApp({ fetchImpl });
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: FB_URL },
    });
    expect(res.statusCode).toBe(429);
    expect(res.json()).toEqual({
      error: { code: "rate_limited", message: "facebook throttled us" },
    });
    await app.close();
  });

  it("maps page_unavailable to 502", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new FetchError("page_unavailable", "HTTP 500 from facebook");
    });
    const { app } = await withApp({ fetchImpl });
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: FB_URL },
    });
    expect(res.statusCode).toBe(502);
    expect(res.json().error.code).toBe("page_unavailable");
    await app.close();
  });

  it("maps an unexpected failure to 500 internal_error", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("boom");
    });
    const { app } = await withApp({ fetchImpl });
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: FB_URL },
    });
    expect(res.statusCode).toBe(500);
    expect(res.json().error.code).toBe("internal_error");
    await app.close();
  });
});
