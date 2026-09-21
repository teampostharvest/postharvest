import type { FastifyInstance, FastifyReply } from "fastify";
import type { Config } from "../config.js";
import { FetchError } from "../errors.js";
import { fetchPage, type FetchPageOptions, type FetchResult } from "../fetch/http-mode.js";
import type { RobotsPolicy } from "../fetch/robots.js";
import type { Bucket } from "../fetch/rate-limiter.js";
import type { FetchRequest, FetchResponse } from "../types.js";

export interface FetchRouteDeps {
  config: Config;
  robots?: RobotsPolicy;
  limiter?: Bucket;
  fetchImpl?: (url: string, opts: FetchPageOptions) => Promise<FetchResult>;
}

const SUPPORTED_MODES = new Set(["http", "browser"]);

export function registerFetchRoutes(
  app: FastifyInstance,
  deps: FetchRouteDeps,
): void {
  app.post<{ Body: FetchRequest }>("/fetch", async (request, reply) => {
    const body = request.body ?? {};

    const target = typeof body.target_url === "string"
      ? body.target_url.trim()
      : "";
    if (target === "") {
      return sendError(reply, 400, "invalid_url", "target_url is required");
    }

    let parsed: URL;
    try {
      parsed = new URL(target);
    } catch {
      return sendError(reply, 400, "invalid_url", "target_url is not a valid URL");
    }
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
      return sendError(reply, 400, "invalid_url", "target_url must be http(s)");
    }
    const host = parsed.hostname.toLowerCase();
    if (host !== "www.facebook.com" && !host.endsWith(".facebook.com")) {
      return sendError(
        reply,
        400,
        "invalid_url",
        "Only facebook.com targets are allowed",
      );
    }

    const mode = body.mode ?? "http";
    if (!SUPPORTED_MODES.has(mode)) {
      return sendError(reply, 400, "invalid_mode", `Unknown mode: ${mode}`);
    }
    if (mode === "browser") {
      return sendError(
        reply,
        501,
        "browser_mode_not_implemented",
        "Browser mode lands with the Playwright transport",
      );
    }
    const accountId = typeof body.account_id === "string" ? body.account_id : null;

    const fetcher = deps.fetchImpl ?? fetchPage;
    const bucketKey =
      accountId && accountId !== ""
        ? `account:${accountId}`
        : `host:${parsed.hostname}`;

    try {
      const result = await fetcher(target, {
        config: deps.config,
        robots: deps.robots,
        limiter: deps.limiter,
        bucketKey,
      });

      const response: FetchResponse = {
        status_code: result.statusCode,
        final_url: result.finalUrl,
        content_type: result.contentType,
        raw_payload: result.body.toString("base64"),
        fetched_at_ms: result.fetchedAtMs,
        // Contract discipline (finalplanv2 §6): http-mode emits the canonical
        // FetchResponse shape — repeated -> [] and missing message -> null.
        updated_cookies: [],
        browser_stats: null,
      };
      return reply.code(200).send(response);
    } catch (err) {
      if (err instanceof FetchError) {
        return sendError(reply, err.statusCode, err.code, err.message);
      }
      app.log.error({ err }, "unexpected fetcher failure");
      return sendError(reply, 500, "internal_error", "Unexpected fetcher failure");
    }
  });
}

function sendError(
  reply: FastifyReply,
  status: number,
  code: string,
  message: string,
) {
  return reply.code(status).send({ error: { code, message } });
}