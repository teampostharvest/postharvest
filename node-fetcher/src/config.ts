/**
 * Environment configuration for node-fetcher.
 *
 * Names mirror the backend settings (backend/core/config.py) so both services
 * can be driven by the same deployment environment.
 */

/** The single honest UA — never rotated, never spoofed. Mirrors backend. */
export const HONEST_USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36";

/** Browser-like request headers, honest and fixed (backend/http_client.py). */
export const BROWSER_HEADERS: Readonly<Record<string, string>> = {
  "User-Agent": HONEST_USER_AGENT,
  "Accept":
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
  "Accept-Encoding": "gzip, deflate, br",
  "Accept-Language": "en-US,en;q=0.9",
  "Sec-Ch-Ua": '"Chromium";v="128", "Not_A Brand";v="24", "Google Chrome";v="128"',
  "Sec-Ch-Ua-Mobile": "?0",
  "Sec-Ch-Ua-Platform": '"Windows"',
  "Sec-Fetch-Dest": "document",
  "Sec-Fetch-Mode": "navigate",
  "Sec-Fetch-Site": "none",
  "Sec-Fetch-User": "?1",
  "Upgrade-Insecure-Requests": "1",
};

export interface Config {
  host: string;
  port: number;
  redisUrl: string | null;
  scraperDelaySeconds: number;
  scraperTimeoutSeconds: number;
  scraperMaxRetries: number;
  scraperRobots: boolean;
  maxBodyBytes: number;
  logLevel: "debug" | "info" | "warn" | "error" | "silent";
}

function num(raw: string | undefined, fallback: number): number {
  if (raw === undefined || raw.trim() === "") return fallback;
  const n = Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

function bool(raw: string | undefined, fallback: boolean): boolean {
  if (raw === undefined || raw.trim() === "") return fallback;
  return !["0", "false", "no", "off"].includes(raw.trim().toLowerCase());
}

const LOG_LEVELS = ["debug", "info", "warn", "error", "silent"] as const;

export function loadConfig(
  env: Record<string, string | undefined> = process.env,
): Config {
  const port = num(env.PORT, 9334);
  const redisUrl =
    env.REDIS_URL && env.REDIS_URL.trim() !== "" ? env.REDIS_URL.trim() : null;
  const logLevelRaw = (env.LOG_LEVEL ?? "info").toLowerCase();
  const logLevel = (LOG_LEVELS as readonly string[]).includes(logLevelRaw)
    ? (logLevelRaw as Config["logLevel"])
    : "info";
  return {
    host: env.HOST ?? "0.0.0.0",
    port,
    redisUrl,
    scraperDelaySeconds: num(env.SCRAPER_DELAY_SECONDS, 2.5),
    scraperTimeoutSeconds: num(env.SCRAPER_TIMEOUT_SECONDS, 20),
    scraperMaxRetries: num(env.SCRAPER_MAX_RETRIES, 3),
    scraperRobots: bool(env.SCRAPER_ROBOTS, true),
    maxBodyBytes: num(env.MAX_BODY_BYTES, 10 * 1024 * 1024),
    logLevel,
  };
}