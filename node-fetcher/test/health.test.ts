import { describe, expect, it, vi } from "vitest";
import { buildApp } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import type { RedisClient } from "../src/redis.js";
import { RedisTokenBucket } from "../src/fetch/rate-limiter.js";

const config = loadConfig({ REDIS_URL: "" });

/** A Redis-shaped client. Needs defineCommand so buildApp can wire the limiter. */
function fakeRedis(pingImpl?: () => Promise<string>): RedisClient {
  const client: Record<string, unknown> = {
    ping: pingImpl ?? (async () => "PONG"),
    defineCommand: vi.fn(),
  };
  return client as unknown as RedisClient;
}

describe("GET /healthz", () => {
  it("reports ok with service metadata", async () => {
    const { app } = buildApp({ config, redis: null });
    const res = await app.inject({ method: "GET", url: "/healthz" });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({
      status: "ok",
      service: "node-fetcher",
    });
    await app.close();
  });
});

describe("GET /readyz", () => {
  it("is ready when redis is unconfigured", async () => {
    const { app } = buildApp({ config, redis: null });
    const res = await app.inject({ method: "GET", url: "/readyz" });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({ status: "ready", redis: "unconfigured" });
    await app.close();
  });

  it("is ready when redis pings", async () => {
    const { app } = buildApp({ config, redis: fakeRedis() });
    const res = await app.inject({ method: "GET", url: "/readyz" });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({ status: "ready", redis: "ok" });
    await app.close();
  });

  it("returns 503 when redis is unreachable", async () => {
    const { app } = buildApp({
      config,
      redis: fakeRedis(async () => {
        throw new Error("connection refused");
      }),
    });
    const res = await app.inject({ method: "GET", url: "/readyz" });
    expect(res.statusCode).toBe(503);
    expect(res.json()).toEqual({ status: "not_ready", redis: "unreachable" });
    await app.close();
  });
});

describe("RedisTokenBucket wiring", () => {
  /** A scriptable client: defineCommand attaches the command to the object. */
  function scriptedClient(): {
    redis: RedisClient;
    consumed: Array<{ key: string; args: string[] }>;
    defineCommand: ReturnType<typeof vi.fn>;
  } {
    const consumed: Array<{ key: string; args: string[] }> = [];
    const client: Record<string, unknown> = { ping: async () => "PONG" };
    const defineCommand = vi.fn(
      (name: string, _opts: { numberOfKeys: number; lua: string }) => {
        if (name === "consumeBucket") {
          client.consumeBucket = async (key: string, ...args: string[]) => {
            consumed.push({ key, args });
            return 1;
          };
        }
      },
    );
    client.defineCommand = defineCommand;
    return {
      redis: client as unknown as RedisClient,
      consumed,
      defineCommand,
    };
  }

  it("registers the Lua script once and consumes against the scripted client", async () => {
    const { redis, consumed, defineCommand } = scriptedClient();
    const bucket = new RedisTokenBucket(redis, {
      capacity: 1,
      refillPerSecond: 0.4,
      now: () => 1_000_000,
    });

    expect(defineCommand).toHaveBeenCalledTimes(1);
    expect(defineCommand.mock.calls[0]?.[0]).toBe("consumeBucket");

    await expect(bucket.tryConsume("host:www.facebook.com", 1)).resolves.toBe(
      true,
    );
    expect(consumed).toHaveLength(1);
    expect(consumed[0]?.key).toBe("ratelimit:host:www.facebook.com");
    expect(consumed[0]?.args[2]).toBe("1"); // tokens to consume
  });

  it("registers the script only once across buckets sharing a client", () => {
    const { redis, defineCommand } = scriptedClient();
    new RedisTokenBucket(redis, { capacity: 1, refillPerSecond: 0.4 });
    new RedisTokenBucket(redis, { capacity: 1, refillPerSecond: 0.4 });
    expect(defineCommand).toHaveBeenCalledTimes(1);
  });

  it("degrades to a local bucket when redis fails", async () => {
    const client: Record<string, unknown> = {
      defineCommand: vi.fn(),
      consumeBucket: async () => {
        throw new Error("ECONNREFUSED");
      },
    };
    const bucket = new RedisTokenBucket(client as unknown as RedisClient, {
      capacity: 1,
      refillPerSecond: 0.4,
      now: () => 1,
    });

    await expect(bucket.tryConsume("host:facebook.com", 1)).resolves.toBe(true);
  });
});