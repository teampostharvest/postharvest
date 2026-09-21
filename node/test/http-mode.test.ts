import { describe, expect, it, vi } from "vitest";
import { fetchPage, type FetchTransport } from "../src/fetch/http-mode.js";
import { FetchError } from "../src/errors.js";
import { loadConfig, HONEST_USER_AGENT } from "../src/config.js";
import type { RobotsDecision, RobotsPolicy } from "../src/fetch/robots.js";

const config = loadConfig({ REDIS_URL: "" });

function bodyStream(data: string | Uint8Array): AsyncIterable<Uint8Array> {
  const bytes =
    typeof data === "string" ? new TextEncoder().encode(data) : data;
  return {
    async *[Symbol.asyncIterator]() {
      yield bytes;
    },
  };
}

function okTransport(overrides: Partial<Parameters<FetchTransport>[0]> = {}) {
  const fn = vi.fn(async (url: string) => ({
    statusCode: 200,
    headers: { "content-type": "text/html" },
    body: bodyStream("<html>ok</html>"),
    ...overrides,
  }));
  return fn as unknown as typeof fn & FetchTransport;
}

function allowAll(): RobotsPolicy {
  return {
    canFetch: vi.fn(async (): Promise<RobotsDecision> => ({ allowed: true })),
  };
}

describe("fetchPage (http mode)", () => {
  it("returns a FetchResult for a 200 with the honest UA", async () => {
    const transport = okTransport();
    const result = await fetchPage("https://www.facebook.com/NASA", {
      config,
      transport,
    });

    expect(result.statusCode).toBe(200);
    expect(result.body.toString("utf8")).toBe("<html>ok</html>");
    expect(result.contentType).toBe("text/html");
    expect(result.finalUrl).toBe("https://www.facebook.com/NASA");

    const init = transport.mock.calls[0]?.[1];
    expect(init?.headers["User-Agent"]).toBe(HONEST_USER_AGENT);
  });

  it("follows 3xx redirects and reports the final URL", async () => {
    const transport = vi.fn(async (url: string) => {
      if (url.endsWith("?locale=en_US")) {
        return {
          statusCode: 200,
          headers: { "content-type": "text/html" },
          body: bodyStream("<html>ok</html>"),
        };
      }
      return {
        statusCode: 302,
        headers: { location: "/NASA?locale=en_US" },
        body: bodyStream(""),
      };
    }) as unknown as FetchTransport;

    const result = await fetchPage("https://www.facebook.com/NASA", {
      config,
      transport,
      delaySeconds: 0,
    });
    expect(result.finalUrl).toBe("https://www.facebook.com/NASA?locale=en_US");
    expect(transport).toHaveBeenCalledTimes(2);
  });

  it("rejects robots.txt disallowed targets before any network call", async () => {
    const transport = okTransport();
    await expect(
      fetchPage("https://www.facebook.com/NASA", {
        config,
        transport,
        robots: {
          canFetch: async () => ({ allowed: false, reason: "nope" }),
        },
      }),
    ).rejects.toMatchObject({ code: "robots_disallowed" });
    expect(transport).not.toHaveBeenCalled();
  });

  it("retries 429 up to maxRetries then fails rate_limited", async () => {
    const transport = okTransport({ statusCode: 429 });
    await expect(
      fetchPage("https://www.facebook.com/NASA", {
        config,
        transport,
        delaySeconds: 0, // no real backoff wait in tests
        maxRetries: 1,
      }),
    ).rejects.toMatchObject({ code: "rate_limited" });
    expect(transport).toHaveBeenCalledTimes(2); // initial + 1 retry
  });

  it("maps repeated 5xx to page_unavailable", async () => {
    const transport = okTransport({ statusCode: 500 });
    await expect(
      fetchPage("https://www.facebook.com/NASA", {
        config,
        transport,
        delaySeconds: 0,
        maxRetries: 0,
      }),
    ).rejects.toMatchObject({ code: "page_unavailable" });
  });

  it("classifies transport timeouts as timeout after retries are spent", async () => {
    const transport = vi.fn(async () => {
      const err = new Error("connection timed out");
      (err as { code?: string }).code = "UND_ERR_HEADERS_TIMEOUT";
      throw err;
    }) as unknown as FetchTransport;

    await expect(
      fetchPage("https://www.facebook.com/NASA", {
        config,
        transport,
        delaySeconds: 0,
        maxRetries: 1,
      }),
    ).rejects.toMatchObject({ code: "timeout" });
  });

  it("decompresses gzip bodies like httpx did", async () => {
    const gz = (await import("node:zlib")).gzipSync(
      Buffer.from("<html>gzipped</html>"),
    );
    const transport = vi.fn(async () => ({
      statusCode: 200,
      headers: { "content-type": "text/html", "content-encoding": "gzip" },
      body: bodyStream(gz),
    })) as unknown as FetchTransport;

    const result = await fetchPage("https://www.facebook.com/NASA", {
      config,
      transport,
    });
    expect(result.body.toString("utf8")).toBe("<html>gzipped</html>");
  });

  it("does not blow past maxBodyBytes on a decompression bomb", async () => {
    const { gzipSync } = await import("node:zlib");
    const big = Buffer.alloc(50 * 1024 * 1024, "a");
    const gz = gzipSync(big);
    const transport = vi.fn(async () => ({
      statusCode: 200,
      headers: { "content-type": "text/html", "content-encoding": "gzip" },
      body: bodyStream(gz),
    })) as unknown as FetchTransport;

    const result = await fetchPage("https://www.facebook.com/NASA", {
      config,
      transport,
      maxBodyBytes: 1024 * 1024,
    });
    // Oversized decode is rejected at the cap — the fetcher degrades by
    // passing raw bytes through rather than crashing or allocating the bomb.
    expect(result.body.length).toBeLessThan(1024 * 1024);
  });

  it("throws invalid_url for a non-facebook host", async () => {
    const transport = okTransport();
    await expect(
      fetchPage("https://example.com", { config, transport }),
    ).rejects.toMatchObject({ code: "invalid_url" });
    expect(transport).not.toHaveBeenCalled();
  });
});