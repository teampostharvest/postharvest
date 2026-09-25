/**
 * Cross-language contract fixtures (finalplanv2.md §6, "Critical Note 8").
 *
 * Node asserts the shared/fixtures golden payloads against the same shapes it
 * emits/serves, so the proto, this service and the fixtures cannot drift.
 * Any change must follow the editing order in shared/fixtures/README.md
 * (proto -> node/src/types.ts -> fixtures).
 */

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const fixturesDir = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "shared",
  "fixtures",
);

const FETCH_RESPONSE_KEYS = [
  "status_code",
  "final_url",
  "content_type",
  "raw_payload",
  "fetched_at_ms",
  "updated_cookies",
  "browser_stats",
] as const;

const FETCH_REQUEST_KEYS = [
  "target_url",
  "mode",
  "account_id",
  "scroll_rounds",
  "max_posts",
  "cookies",
] as const;

const BROWSER_STATS_KEYS = ["login_wall", "feed_missing", "posts_found"] as const;

function fixturesPath(name: string): string {
  return path.join(fixturesDir, name);
}

function loadFixture<T>(name: string): T {
  return JSON.parse(readFileSync(fixturesPath(name), "utf8")) as T;
}

function loadAsset(name: string): Buffer {
  return readFileSync(fixturesPath(name));
}

function expectExactKeys(obj: Record<string, unknown>, keys: readonly string[]) {
  expect(Object.keys(obj).sort()).toEqual([...keys].sort());
}

describe("shared/fixtures contract", () => {
  it("http FetchResponse fixture is the canonical http-mode shape", () => {
    const fixture = loadFixture<Record<string, unknown>>(
      "fetch_response.html.json",
    );
    expectExactKeys(fixture, FETCH_RESPONSE_KEYS);
    expect(fixture.content_type).toBe("text/html");
    // proto3 JSON defaults for the browser-only fields (§6).
    expect(fixture.updated_cookies).toEqual([]);
    expect(fixture.browser_stats).toBeNull();
    const html = Buffer.from(String(fixture.raw_payload), "base64").toString(
      "utf8",
    );
    expect(html.length).toBeGreaterThan(0);
  });

  it("browser FetchResponse fixture decodes to the exact snapshot asset", () => {
    const fixture = loadFixture<Record<string, unknown>>(
      "fetch_response.browser.json",
    );
    expectExactKeys(fixture, FETCH_RESPONSE_KEYS);
    expect(fixture.content_type).toBe("text/html");
    expect(Array.isArray(fixture.updated_cookies)).toBe(true);
    expect(fixture.updated_cookies?.length).toBeGreaterThan(0);

    const stats = fixture.browser_stats as Record<string, unknown>;
    expect(stats).not.toBeNull();
    expectExactKeys(stats, BROWSER_STATS_KEYS);
    expect(typeof stats.login_wall).toBe("boolean");
    expect(typeof stats.feed_missing).toBe("boolean");
    expect(typeof stats.posts_found).toBe("number");

    // raw_payload is base64 of browser_snapshot.html, byte-for-byte.
    const snapshot = loadAsset("browser_snapshot.html");
    const decoded = Buffer.from(String(fixture.raw_payload), "base64");
    expect(decoded.equals(snapshot)).toBe(true);

    const asText = decoded.toString("utf8");
    // The Python parser consumes these markers; they must be present so the
    // Node capture can be byte-parity with fetch_with_browser.
    expect(asText).toContain('data-fb-graphql-feed="1"');
    expect(asText).toContain('"post_id":"2001"');
    // Feed captured -> the missing-feed marker must NOT be present.
    expect(asText).not.toContain("<!-- fb-scrape-feed-missing -->");
  });

  it("browser FetchRequest fixture carries the browser-mode options", () => {
    const fixture = loadFixture<Record<string, unknown>>(
      "fetch_request.browser.json",
    );
    expectExactKeys(fixture, FETCH_REQUEST_KEYS);
    expect(fixture.mode).toBe("browser");
    expect(fixture.account_id).toBe("ops:maverick");
    expect(typeof fixture.scroll_rounds).toBe("number");
    expect(typeof fixture.max_posts).toBe("number");
    expect(Array.isArray(fixture.cookies)).toBe(true);
    expect(fixture.cookies?.length).toBe(2);
  });

  it("ParseRequest html fixture carries page context for the Go worker", () => {
    const fixture = loadFixture<Record<string, unknown>>(
      "parse_request.html.json",
    );
    expectExactKeys(fixture, [
      "raw_payload",
      "content_type",
      "idempotency_key",
      "target_url",
      "handle",
    ]);
    expect(fixture.content_type).toBe("html");
    expect(fixture.idempotency_key).toBe("job_7:acmewidgets_1");
    expect(fixture.target_url).toBe("https://www.facebook.com/acmewidgets");
    expect(fixture.handle).toBe("acmewidgets");
    // Consistency rule (shared/fixtures/README.md): raw_payload decodes to
    // the exact bytes of golang/parser/testdata/dom_sample.html.
    const decoded = Buffer.from(String(fixture.raw_payload), "base64");
    const dom = readFileSync(
      path.join(
        path.dirname(fileURLToPath(import.meta.url)),
        "..",
        "..",
        "golang",
        "parser",
        "testdata",
        "dom_sample.html",
      ),
    );
    expect(decoded.toString("utf8").replace(/\r\n/g, "\n")).toBe(
      dom.toString("utf8").replace(/\r\n/g, "\n"),
    );
  });
});