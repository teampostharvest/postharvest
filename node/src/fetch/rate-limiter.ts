/**
 * Token-bucket rate limiting.
 *
 * See backend/scraper/rate_limiter.py for the reference semantics: default
 * floor of one request per `SCRAPER_DELAY_SECONDS` (2.5 s), shared across all
 * instances and keyed per account/host (finalplanv2.md §8a). In-process bucket
 * is the hermetic/dev default; the Redis variant is the multi-instance one.
 */

import type { RedisClient } from "../redis.js";
import { FetchError } from "../errors.js";

export interface Bucket {
  /** Attempt to consume `tokens` (default 1). Resolves true if granted. */
  tryConsume(key: string, tokens?: number): Promise<boolean>;
}

export interface TokenBucketOptions {
  capacity: number;
  refillPerSecond: number;
  now?: () => number;
}

interface BucketState {
  tokens: number;
  last: number;
}

/** In-process per-key token bucket — dev/hermetic fallback. */
export class LocalTokenBucket implements Bucket {
  private readonly buckets = new Map<string, BucketState>();
  private readonly now: () => number;

  constructor(private readonly opts: TokenBucketOptions) {
    this.now = opts.now ?? Date.now;
  }

  async tryConsume(key: string, tokens = 1): Promise<boolean> {
    const now = this.now();
    const state =
      this.buckets.get(key) ?? { tokens: this.opts.capacity, last: now };
    const elapsedSec = Math.max(0, (now - state.last) / 1000);
    state.tokens = Math.min(
      this.opts.capacity,
      state.tokens + elapsedSec * this.opts.refillPerSecond,
    );
    state.last = now;
    if (state.tokens >= tokens) {
      state.tokens -= tokens;
      this.buckets.set(key, state);
      return true;
    }
    this.buckets.set(key, state);
    return false;
  }
}

/**
 * Redis-backed token bucket (shared across fetcher instances).
 *
 * One Lua script per key for atomic refill+consume under `ratelimit:{key}`.
 * `defineCommand` is registered once per client instance.
 */
const CONSUME_LUA = `
local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens') or '0')
local last = tonumber(redis.call('HGET', KEYS[1], 'last') or '0')
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local need = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])
if last == 0 then last = now end
tokens = math.min(capacity, tokens + ((now - last) / 1000) * rate)
local granted = 0
if tokens >= need then
  tokens = tokens - need
  granted = 1
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'last', tostring(now))
redis.call('EXPIRE', KEYS[1], ttl)
return granted
`;

export interface RedisBucketOptions {
  capacity: number;
  refillPerSecond: number;
  ttlSeconds?: number;
  now?: () => number;
}

interface ScriptableRedis extends RedisClient {
  consumeBucket?: (
    key: string,
    capacity: string,
    rate: string,
    tokens: string,
    now: string,
    ttl: string,
  ) => Promise<unknown>;
}

export class RedisTokenBucket implements Bucket {
  private readonly redis: ScriptableRedis;
  private readonly ttlSeconds: number;
  private readonly now: () => number;

  constructor(
    redis: RedisClient,
    private readonly opts: RedisBucketOptions,
  ) {
    this.redis = redis as ScriptableRedis;
    this.ttlSeconds = opts.ttlSeconds ?? Math.max(60, Math.ceil(60 / (opts.refillPerSecond || 0.4)));
    this.now = opts.now ?? Date.now;
    if (!this.redis.consumeBucket) {
      redis.defineCommand("consumeBucket", {
        numberOfKeys: 1,
        lua: CONSUME_LUA,
      });
    }
  }

  async tryConsume(key: string, tokens = 1): Promise<boolean> {
    try {
      const result = await this.redis.consumeBucket!(
        `ratelimit:${key}`,
        String(this.opts.capacity),
        String(this.opts.refillPerSecond),
        String(tokens),
        String(this.now()),
        String(this.ttlSeconds),
      );
      return Number(result) === 1;
    } catch (err) {
      // Redis unavailable must never hard-block the fetcher. Degrade to a
      // per-process bucket for the life of this request (backend parity:
      // missing Redis = degraded, not down).
      const fallback =
        this.fallback ?? (this.fallback = new LocalTokenBucket(this.opts));
      return fallback.tryConsume(key, tokens);
    }
  }

  private fallback: LocalTokenBucket | null = null;
}

export interface WaitOptions {
  timeoutMs?: number;
  pollMs?: number;
}

/** Wait until a token is granted, then return. Throws `rate_limited` on timeout. */
export async function waitForToken(
  bucket: Bucket,
  key: string,
  opts: WaitOptions = {},
): Promise<void> {
  const timeoutMs = opts.timeoutMs ?? 30_000;
  const pollMs = opts.pollMs ?? 250;
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (await bucket.tryConsume(key, 1)) return;
    if (Date.now() >= deadline) {
      throw new FetchError(
        "rate_limited",
        `Timed out waiting for a rate-limit token (${timeoutMs} ms)`,
      );
    }
    await sleep(pollMs);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}