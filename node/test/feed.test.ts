import { describe, expect, it, vi } from "vitest";
import { buildApp } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import { FetchError } from "../src/errors.js";
import {
  buildFrameRequest,
  hasBlockMarker,
  looksBlocked,
  type FeedWalker,
} from "../src/feed/walk.js";
import type { FeedFrame } from "../src/feed/types.js";

const config = loadConfig({ REDIS_URL: "", SCRAPER_ROBOTS: "0" });

const FB_URL = "https://www.facebook.com/NASA";

function frame(overrides: Partial<FeedFrame> = {}): FeedFrame {
  return {
    statusCode: 200,
    finalUrl: FB_URL,
    body: Buffer.from("<html><body>frame</body></html>", "utf8"),
    sessionId: "sess-abc",
    blocked: false,
    ...overrides,
  };
}

function stubWalker(impl?: FeedWalker): FeedWalker {
  return (
    impl ?? {
      fetchFrame: vi.fn(async () => frame()),
      close: vi.fn(async () => {}),
    }
  );
}

async function withApp(walker?: FeedWalker) {
  return buildApp({
    config,
    redis: null,
    feedWalker: walker ?? stubWalker(),
  });
}

describe("buildFrameRequest", () => {
  it("builds a GET frame with browser headers", () => {
    const req = buildFrameRequest(FB_URL);
    expect(req.method).toBeUndefined();
    expect(req.url).toBe(FB_URL);
    expect(req.headers["User-Agent"]).toBeTruthy();
    expect(req.payload).toBeUndefined();
  });

  it("builds a POST frame with form-encoded payload and GraphQL headers", () => {
    const req = buildFrameRequest("https://www.facebook.com/api/graphql/", {
      method: "POST",
      form: { doc_id: "28338492715759825", variables: '{"count":3}' },
      referer: "https://www.facebook.com/NASA/",
    });
    expect(req.method).toBe("POST");
    expect(req.url).toBe("https://www.facebook.com/api/graphql/");
    expect(req.headers["Content-Type"]).toBe("application/x-www-form-urlencoded");
    expect(req.headers["Origin"]).toBe("https://www.facebook.com");
    expect(req.headers["Referer"]).toBe("https://www.facebook.com/NASA/");
    expect(req.headers["Sec-Fetch-Mode"]).toBe("cors");
    expect(req.payload).toBe(
      "doc_id=28338492715759825&variables=%7B%22count%22%3A3%7D",
    );
  });
});

describe("looksBlocked / hasBlockMarker", () => {
  it("flags 401/403/429 as blocked", () => {
    expect(looksBlocked(403, "<html>nope</html>")).toBe(true);
    expect(looksBlocked(429, "anything")).toBe(true);
    expect(looksBlocked(401, "anything")).toBe(true);
  });

  it("flags high-signal markers on 2xx pages", () => {
    expect(
      looksBlocked(200, "<title>We've detected unusual traffic from your computer network</title>"),
    ).toBe(true);
    expect(hasBlockMarker("log into facebook to continue")).toBe(true);
    expect(hasBlockMarker("you have been temporarily blocked")).toBe(true);
  });

  it("does not flag clean frames or 404/5xx", () => {
    expect(looksBlocked(200, "<html>posts go here</html>")).toBe(false);
    expect(looksBlocked(404, "not found")).toBe(false);
    expect(looksBlocked(500, "server error")).toBe(false);
    expect(hasBlockMarker("")).toBe(false);
  });
});

describe("POST /feed-fetch", () => {
  it("rejects a missing target_url", async () => {
    const { app } = await withApp();
    const res = await app.inject({
      method: "POST",
      url: "/feed-fetch",
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
      url: "/feed-fetch",
      payload: { target_url: "not a url" },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.code).toBe("invalid_url");
    await app.close();
  });

  it("rejects non-facebook hosts", async () => {
    const { app } = await withApp();
    const res = await app.inject({
      method: "POST",
      url: "/feed-fetch",
      payload: { target_url: "https://example.com/NASA" },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.message).toContain("facebook.com");
    await app.close();
  });

  it("returns the raw frame envelope on success", async () => {
    const walker = stubWalker({
      fetchFrame: vi.fn(async () => frame({ sessionId: "sess-1" })),
      close: vi.fn(async () => {}),
    });
    const { app } = await withApp(walker);
    const res = await app.inject({
      method: "POST",
      url: "/feed-fetch",
      payload: { target_url: FB_URL },
    });
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.status_code).toBe(200);
    expect(body.final_url).toBe(FB_URL);
    expect(body.session_id).toBe("sess-1");
    expect(body.blocked).toBe(false);
    expect(Buffer.from(body.raw_payload, "base64").toString("utf8")).toBe(
      "<html><body>frame</body></html>",
    );
    expect(walker.fetchFrame).toHaveBeenCalledWith(
      FB_URL,
      expect.objectContaining({ bucketKey: "host:www.facebook.com" }),
    );
    await app.close();
  });

  it("surfaces a blocked frame verdict in the envelope", async () => {
    const walker = stubWalker({
      fetchFrame: vi.fn(async () =>
        frame({ statusCode: 403, blocked: true, sessionId: "sess-2" }),
      ),
      close: vi.fn(async () => {}),
    });
    const { app } = await withApp(walker);
    const res = await app.inject({
      method: "POST",
      url: "/feed-fetch",
      payload: { target_url: FB_URL },
    });
    expect(res.statusCode).toBe(200);
    expect(res.json().blocked).toBe(true);
    expect(res.json().status_code).toBe(403);
    await app.close();
  });

  it("maps FetchError to the unified error envelope", async () => {
    const walker = stubWalker({
      fetchFrame: vi.fn(async () => {
        throw new FetchError("rate_limited", "Facebook returned HTTP 429");
      }),
      close: vi.fn(async () => {}),
    });
    const { app } = await withApp(walker);
    const res = await app.inject({
      method: "POST",
      url: "/feed-fetch",
      payload: { target_url: FB_URL },
    });
    expect(res.statusCode).toBe(429);
    expect(res.json()).toEqual({
      error: { code: "rate_limited", message: "Facebook returned HTTP 429" },
    });
    await app.close();
  });

  it("maps unexpected walker failures to internal_error", async () => {
    const walker = stubWalker({
      fetchFrame: vi.fn(async () => {
        throw new Error("boom");
      }),
      close: vi.fn(async () => {}),
    });
    const { app } = await withApp(walker);
    const res = await app.inject({
      method: "POST",
      url: "/feed-fetch",
      payload: { target_url: FB_URL },
    });
    expect(res.statusCode).toBe(500);
    expect(res.json()).toEqual({
      error: { code: "internal_error", message: "Unexpected feed walker failure" },
    });
    await app.close();
  });
});

describe("POST /feed-fetch — GraphQL pagination frames (Phase 2)", () => {
  it("rejects an unsupported method", async () => {
    const { app } = await withApp();
    const res = await app.inject({
      method: "POST",
      url: "/feed-fetch",
      payload: { target_url: FB_URL, method: "PUT" },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.code).toBe("invalid_request");
    await app.close();
  });

  it("rejects form without an explicit POST method", async () => {
    const { app } = await withApp();
    const res = await app.inject({
      method: "POST",
      url: "/feed-fetch",
      payload: { target_url: FB_URL, form: { doc_id: "1" } },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.message).toContain("POST");
    await app.close();
  });

  it("rejects non-string form values", async () => {
    const { app } = await withApp();
    const res = await app.inject({
      method: "POST",
      url: "/feed-fetch",
      payload: {
        target_url: FB_URL,
        method: "POST",
        form: { doc_id: 123 as unknown as string },
      },
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.message).toContain("string");
    await app.close();
  });

  it("forwards method/form/referer to the walker on a POST frame", async () => {
    const walker = stubWalker({
      fetchFrame: vi.fn(async () => frame({ sessionId: "sess-gql" })),
      close: vi.fn(async () => {}),
    });
    const { app } = await withApp(walker);
    const res = await app.inject({
      method: "POST",
      url: "/feed-fetch",
      payload: {
        target_url: "https://www.facebook.com/api/graphql/",
        cursor: '{"kind":"gql"}',
        method: "POST",
        form: { doc_id: "28338492715759825", variables: '{"count":3}' },
        referer: FB_URL,
      },
    });
    expect(res.statusCode).toBe(200);
    expect(res.json().session_id).toBe("sess-gql");
    expect(walker.fetchFrame).toHaveBeenCalledWith(
      "https://www.facebook.com/api/graphql/",
      expect.objectContaining({
        method: "POST",
        form: { doc_id: "28338492715759825", variables: '{"count":3}' },
        referer: FB_URL,
        bucketKey: "host:www.facebook.com",
      }),
    );
    await app.close();
  });

  it("defaults to GET without a method", async () => {
    const walker = stubWalker();
    const { app } = await withApp(walker);
    await app.inject({
      method: "POST",
      url: "/feed-fetch",
      payload: { target_url: FB_URL },
    });
    expect(walker.fetchFrame).toHaveBeenCalledWith(
      FB_URL,
      expect.objectContaining({ method: "GET" }),
    );
    await app.close();
  });
});