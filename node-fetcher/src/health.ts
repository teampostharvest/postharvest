import type { FastifyInstance } from "fastify";
import type { Config } from "./config.js";
import type { RedisClient } from "./redis.js";

export interface HealthDeps {
  config: Config;
  redis: RedisClient | null;
}

export function registerHealthRoutes(
  app: FastifyInstance,
  deps: HealthDeps,
): void {
  app.get("/healthz", async () => ({
    status: "ok",
    service: "node-fetcher",
    version: "0.1.0",
    uptime_s: Math.round(process.uptime()),
  }));

  app.get("/readyz", async (_request, reply) => {
    if (deps.redis === null) {
      return { status: "ready", redis: "unconfigured" };
    }
    try {
      await deps.redis.ping();
      return { status: "ready", redis: "ok" };
    } catch {
      return reply.code(503).send({ status: "not_ready", redis: "unreachable" });
    }
  });
}