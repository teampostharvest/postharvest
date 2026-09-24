/**
 * node metrics (plans/monitoring.md) — Prometheus exposition at GET /metrics.
 *
 * Uses a dedicated Registry (not prom-client's global default) so vitest
 * files isolate cleanly and the scrape only sees this service's counters.
 */

import { Counter, Registry } from "prom-client";
import type { FastifyInstance, FastifyRequest } from "fastify";

export const register = new Registry();

export const fetchRequests = new Counter({
  name: "postharvest_node_fetch_requests_total",
  help: "Fetch requests towards facebook.com by outcome.",
  labelNames: ["outcome"] as const, // success | rejected | error
  registers: [register],
});

type RouteInfo = { routeOptions?: { url?: string } };

/**
 * Register GET /metrics plus an onResponse hook that counts /fetch outcomes.
 *
 * Outcomes: success (200), rejected (transport 4xx), error (5xx). Counting
 * happens in the hook so route handlers stay untouched and every terminal
 * status — including early validation 400s — is captured exactly once.
 */
export function registerMetricsHooks(app: FastifyInstance): void {
  app.get("/metrics", async (_request, reply) => {
    reply.header("content-type", register.contentType);
    return register.metrics();
  });

  app.addHook("onResponse", async (request, reply) => {
    const route = (request as FastifyRequest & RouteInfo);
    const path = route.routeOptions?.url ?? request.raw.url ?? request.url;
    if (path.split("?")[0] !== "/fetch") {
      return;
    }
    const code = reply.statusCode;
    const outcome = code === 200 ? "success" : code >= 500 ? "error" : "rejected";
    fetchRequests.labels({ outcome }).inc();
  });
}