import { describe, expect, it } from "vitest";
import { buildApp } from "../src/server.js";
import { loadConfig } from "../src/config.js";

const config = loadConfig({ REDIS_URL: "" });

describe("GET /metrics", () => {
  it("serves prometheus text with the fetch counter", async () => {
    const { app } = buildApp({ config, redis: null });
    const res = await app.inject({ method: "GET", url: "/metrics" });
    expect(res.statusCode).toBe(200);
    expect(res.headers["content-type"]).toContain("text/plain");
    expect(res.body).toContain("postharvest_node_fetch_requests_total");
    await app.close();
  });

  it("counts /fetch outcomes by status class", async () => {
    const { app } = buildApp({ config, redis: null });

    // invalid_url -> 400 -> rejected
    await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: "" },
    });
    // ok-but-erroring request -> 5xx -> error (fetchImpl points nowhere real,
    // so rely on validation rejection only for the deterministic case)
    const res = await app.inject({
      method: "POST",
      url: "/fetch",
      payload: { target_url: "not-a-url" },
    });
    expect(res.statusCode).toBe(400);

    const body = (await app.inject({ method: "GET", url: "/metrics" })).body;
    expect(body).toContain(
      'postharvest_node_fetch_requests_total{outcome="rejected"}',
    );
    await app.close();
  });
});