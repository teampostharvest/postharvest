import { describe, expect, it } from "vitest";
import {
  LocalTokenBucket,
  waitForToken,
  type TokenBucketOptions,
} from "../src/fetch/rate-limiter.js";

function makeBucket(opts: Partial<TokenBucketOptions> = {}): {
  tb: LocalTokenBucket;
  clock: { t: number; advance: (ms: number) => void };
} {
  const clock = { t: 0 };
  return {
    tb: new LocalTokenBucket({
      capacity: 1,
      refillPerSecond: 1 / 2500, // one token per 2.5 s — Python default parity
      now: () => clock.t,
      ...opts,
    }),
    clock: {
      t: 0,
      advance: (ms: number) => {
        clock.t += ms;
      },
    },
  };
}

describe("LocalTokenBucket", () => {
  it("grants up to capacity immediately", async () => {
    const { tb } = makeBucket({ capacity: 2, refillPerSecond: 1 });
    await expect(tb.tryConsume("k", 2)).resolves.toBe(true);
    await expect(tb.tryConsume("k", 1)).resolves.toBe(false);
  });

  it("refills over elapsed time", async () => {
    const { tb, clock } = makeBucket({ capacity: 1, refillPerSecond: 1 });
    await expect(tb.tryConsume("k")).resolves.toBe(true);
    await expect(tb.tryConsume("k")).resolves.toBe(false);
    clock.advance(1000); // 1 second at rate 1/s => 1 token
    await expect(tb.tryConsume("k")).resolves.toBe(true);
  });

  it("keys buckets independently", async () => {
    const { tb } = makeBucket({ capacity: 1, refillPerSecond: 1 });
    await expect(tb.tryConsume("a")).resolves.toBe(true);
    await expect(tb.tryConsume("b")).resolves.toBe(true);
    await expect(tb.tryConsume("a")).resolves.toBe(false);
  });
});

describe("waitForToken", () => {
  it("returns when a token becomes available after refill", async () => {
    const clock = { t: 0 };
    const bucket = new LocalTokenBucket({
      capacity: 1,
      refillPerSecond: 1 / 1000,
      now: () => clock.t,
    });
    const promise = waitForToken(bucket, "k", { timeoutMs: 10_000 });
    clock.t += 1500; // enough time passes for a token to refill
    await expect(promise).resolves.toBeUndefined();
  });
});