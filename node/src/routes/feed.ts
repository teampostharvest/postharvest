/**
 * POST /feed-fetch — one feed frame via the Crawlee feed walker.
 *
 * Same envelope discipline as /fetch (sendError mapping onto the unified
 * error codes); visitors of this route must never parse the frame — it is
 * raw bytes handed to FastAPI for Python-side parsing.
 */

import type { FastifyInstance, FastifyReply } from "fastify";
import type { Config } from "../config.js";
import { FetchError } from "../errors.js";
import type { Bucket } from "../fetch/rate-limiter.js";
import type { FeedFrame, FeedFrameRequest, FeedFrameResponse } from "../feed/types.js";
import type { FeedWalker } from "../feed/walk.js";

export interface FeedRouteDeps {
  config: Config;
  limiter?: Bucket;
  walker: FeedWalker;
}

export function registerFeedRoutes(
  app: FastifyInstance,
  deps: FeedRouteDeps,
): void {
  app.post<{ Body: FeedFrameRequest }>(
    "/feed-fetch",
    async (request, reply) => {
      const body = request.body ?? {};

      const target =
        typeof body.target_url === "string" ? body.target_url.trim() : "";
      if (target === "") {
        return sendError(reply, 400, "invalid_url", "target_url is required");
      }

      let parsed: URL;
      try {
        parsed = new URL(target);
      } catch {
        return sendError(
          reply,
          400,
          "invalid_url",
          "target_url is not a valid URL",
        );
      }
      if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
        return sendError(
          reply,
          400,
          "invalid_url",
          "target_url must be an http(s) URL",
        );
      }
      const host = parsed.hostname.toLowerCase();
      if (host !== "www.facebook.com" && !host.endsWith(".facebook.com")) {
        return sendError(
          reply,
          400,
          "invalid_url",
          `Only facebook.com targets are allowed (got ${host})`,
        );
      }

      // -- method / form / referer (POST GraphQL pagination frames) -----
      const method =
        typeof body.method === "string" ? body.method.toUpperCase() : "GET";
      if (method !== "GET" && method !== "POST") {
        return sendError(
          reply,
          400,
          "invalid_request",
          "method must be GET or POST",
        );
      }
      let form: Record<string, string> | undefined;
      if (body.form !== undefined && body.form !== null) {
        if (method !== "POST") {
          return sendError(
            reply,
            400,
            "invalid_request",
            "form is only valid for POST frames",
          );
        }
        if (
          typeof body.form !== "object" ||
          Array.isArray(body.form) ||
          Object.values(body.form).some((v) => typeof v !== "string")
        ) {
          return sendError(
            reply,
            400,
            "invalid_request",
            "form must be an object of string values",
          );
        }
        form = body.form as Record<string, string>;
      }
      const referer =
        typeof body.referer === "string" && body.referer.trim() !== ""
          ? body.referer
          : undefined;

      let frame: FeedFrame;
      try {
        frame = await deps.walker.fetchFrame(parsed.toString(), {
          limiter: deps.limiter,
          bucketKey: `host:${host}`,
          method: method === "POST" ? "POST" : "GET",
          form,
          referer,
        });
      } catch (err) {
        if (err instanceof FetchError) {
          return sendError(reply, err.statusCode, err.code, err.message);
        }
        app.log.error({ err }, "unexpected feed walker failure");
        return sendError(
          reply,
          500,
          "internal_error",
          "Unexpected feed walker failure",
        );
      }

      const response: FeedFrameResponse = {
        status_code: frame.statusCode,
        final_url: frame.finalUrl,
        raw_payload: frame.body.toString("base64"),
        session_id: frame.sessionId,
        blocked: frame.blocked,
        fetched_at_ms: Date.now(),
      };
      return reply.code(200).send(response);
    },
  );
}

function sendError(
  reply: FastifyReply,
  status: number,
  code: string,
  message: string,
) {
  return reply.code(status).send({ error: { code, message } });
}