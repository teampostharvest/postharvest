import { Redis } from "ioredis";

export type RedisClient = Redis;

let singleton: Redis | null = null;

/**
 * Lazily create one shared Redis client. Returns null when REDIS_URL is not
 * configured — same optional degradation as the backend (a missing Redis must
 * never take the fetcher down; rate limiting falls back to in-process).
 */
export function getRedis(url: string | null | undefined): Redis | null {
  if (!url) return null;
  if (singleton) return singleton;
  const client = new Redis(url, {
    maxRetriesPerRequest: 1,
    enableOfflineQueue: false,
    lazyConnect: false,
  });
  // Errors are surfaced via the /readyz ping and the limiter; never crash the
  // process on connection noise.
  client.on("error", () => {
    /* swallowed — see getRedis callers */
  });
  singleton = client;
  return singleton;
}

export async function closeRedis(): Promise<void> {
  if (singleton) {
    const client = singleton;
    singleton = null;
    await client.quit().catch(() => undefined);
  }
}