/**
 * node — the only service that talks to Facebook (finalplanv2.md §4).
 *
 * App factory + entrypoint. Internal-only service: FastAPI talks to it, it
 * never talks back to the public web except for the configured Facebook
 * targets (plus each host's robots.txt).
 */

import Fastify, { type FastifyInstance } from "fastify";
import { loadConfig, type Config } from "./config.js";
import { registerHealthRoutes } from "./health.js";
import { registerFetchRoutes } from "./routes/fetch.js";
import { closeRedis, getRedis, type RedisClient } from "./redis.js";
import { LocalTokenBucket, RedisTokenBucket, type Bucket } from "./fetch/rate-limiter.js";
import { RobotsTxtPolicy, type RobotsPolicy } from "./fetch/robots.js";
import { fetchPage, type FetchPageOptions, type FetchResult } from "./fetch/http-mode.js";
import type { BrowserOpenFn } from "./browser-mode/types.js";

export interface BuildAppOptions {
  config?: Config;
  redis?: RedisClient | null;
  limiter?: Bucket;
  robots?: RobotsPolicy;
  fetchImpl?: (url: string, opts: FetchPageOptions) => Promise<FetchResult>;
  browserOpen?: BrowserOpenFn;
}

export interface BuiltApp {
  app: FastifyInstance;
  config: Config;
  redis: RedisClient | null;
}

export function buildApp(opts: BuildAppOptions = {}): BuiltApp {
  const config = opts.config ?? loadConfig();
  // Explicit null means "no Redis" (tests/dev); undefined asks for a lazy
  // shared client bound to config.redisUrl.
  const redis =
    opts.redis !== undefined ? opts.redis : getRedis(config.redisUrl);

  const limiter: Bucket =
    opts.limiter ??
    (redis
      ? new RedisTokenBucket(redis, {
          capacity: 1,
          refillPerSecond: 1 / config.scraperDelaySeconds,
        })
      : new LocalTokenBucket({
          capacity: 1,
          refillPerSecond: 1 / config.scraperDelaySeconds,
        }));

  const robots: RobotsPolicy | undefined =
    opts.robots === undefined
      ? config.scraperRobots
        ? new RobotsTxtPolicy()
        : undefined
      : opts.robots;

  const app = Fastify({
    logger: config.logLevel === "debug" ? { level: "debug" } : false,
    trustProxy: false,
  });

  registerHealthRoutes(app, { config, redis });
  registerFetchRoutes(app, {
    config,
    robots,
    limiter,
    fetchImpl: opts.fetchImpl,
    browserOpen: opts.browserOpen,
  });

  return { app, config, redis };
}

export async function start(): Promise<void> {
  const { app, config, redis } = buildApp();
  try {
    await app.listen({ host: config.host, port: config.port });
    app.log.info(
      { port: config.port, redis: redis ? "configured" : "unconfigured" },
      "node listening",
    );
  } catch (err) {
    app.log.error(err, "failed to start");
    process.exitCode = 1;
    return;
  }

  const shutdown = async (signal: string) => {
    app.log.info({ signal }, "shutting down");
    await app.close();
    await closeRedis();
    process.exit(0);
  };
  process.once("SIGINT", () => void shutdown("SIGINT"));
  process.once("SIGTERM", () => void shutdown("SIGTERM"));
}

// Entrypoint when run directly (tsx / compiled node).
if (import.meta.url === `file://${process.argv[1]}`) {
  void start();
}