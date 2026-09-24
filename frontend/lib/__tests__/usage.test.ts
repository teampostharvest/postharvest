import { describe, expect, it } from "vitest";
import {
  USAGE_RESET_LABEL,
  formatUpdateTime,
  planLabel,
  usagePillParts,
  usageTone,
  usageValue,
} from "@/lib/usage";
import type { UsageResponse } from "@/lib/types";

/**
 * Acceptance matrix (plans/usagecomp.md): `(current, max)` pairs → tone,
 * including `max = null` (unlimited), `current/max >= 0.5`, `< 0.5`, and
 * `current == max`.
 */
describe("usageTone — bounded/unbounded color rule", () => {
  it("healthy: at least 50% of capacity remaining", () => {
    expect(usageTone(0, 5)).toBe("healthy"); // 5/5 remaining
    expect(usageTone(2, 5)).toBe("healthy"); // 3/5 remaining
    expect(usageTone(0, 10)).toBe("healthy");
  });

  it("warning: under 50% remaining but not exhausted", () => {
    expect(usageTone(3, 5)).toBe("warning"); // 2/5 remaining
    expect(usageTone(4, 5)).toBe("warning"); // 1/5 remaining
    expect(usageTone(8, 10)).toBe("warning");
  });

  it("exhausted: zero remaining (including over-cap)", () => {
    expect(usageTone(5, 5)).toBe("exhausted");
    expect(usageTone(10, 10)).toBe("exhausted");
    expect(usageTone(6, 5)).toBe("exhausted"); // over the ceiling still reads exhausted
  });

  it("max = null / undefined means unlimited — never green", () => {
    expect(usageTone(2, null)).toBe("unbounded");
    expect(usageTone(0, null)).toBe("unbounded");
    expect(usageTone(0, undefined)).toBe("unbounded");
  });

  it("boundary: golden 50% remaining keeps healthy, just below flips warning", () => {
    expect(usageTone(2, 4)).toBe("healthy"); // exactly 2/4 remaining
    expect(usageTone(3, 4)).toBe("warning"); // 1/4 remaining
  });
});

describe("usageValue — value formatting with bold-count parts", () => {
  it('"of" style: current count + " of max"', () => {
    expect(usageValue(0, 5, "of")).toEqual({ head: "0", tail: " of 5", tone: "healthy" });
    expect(usageValue(2, null, "of")).toEqual({
      head: "2",
      tail: " of unlimited",
      tone: "unbounded",
    });
  });

  it('"slash" style: current count + "/max"', () => {
    expect(usageValue(0, null, "slash")).toEqual({
      head: "0",
      tail: "/unlimited",
      tone: "unbounded",
    });
    expect(usageValue(0, 3, "slash")).toEqual({ head: "0", tail: "/3", tone: "healthy" });
  });

  it('"free" style: remaining slots + " free"', () => {
    expect(usageValue(0, 10, "free")).toEqual({ head: "10", tail: " free", tone: "healthy" });
    expect(usageValue(7, 10, "free")).toEqual({ head: "3", tail: " free", tone: "warning" });
    expect(usageValue(10, 10, "free")).toEqual({ head: "0", tail: " free", tone: "exhausted" });
  });

  it("never formats a negative free count", () => {
    expect(usageValue(12, 10, "free")).toEqual({ head: "0", tail: " free", tone: "exhausted" });
  });
});

describe("usagePillParts — pill body", () => {
  const usage: UsageResponse = {
    plan: "pro",
    jobs_running: { used: 0, limit: 3 },
    personal_accounts: { used: 2, limit: 5 },
    per_job: { urls: 50, max_posts: 5000 },
    posts_today: 0,
    jobs_today: 4,
  };

  it("maps plan + personal pool + concurrent jobs", () => {
    expect(usagePillParts(usage)).toEqual({
      plan: "Pro",
      pool: { head: "2", tail: "/5", tone: "healthy" },
      jobs: { head: "0", tail: "/3", tone: "healthy" },
    });
  });

  it("renders unlimited ceilings honestly", () => {
    expect(
      usagePillParts({
        ...usage,
        plan: "enterprise",
        personal_accounts: { used: 2, limit: null },
      }),
    ).toEqual({
      plan: "Enterprise",
      pool: { head: "2", tail: "/unlimited", tone: "unbounded" },
      jobs: { head: "0", tail: "/3", tone: "healthy" },
    });
  });

  it("returns null without data", () => {
    expect(usagePillParts(null)).toBeNull();
    expect(usagePillParts(undefined)).toBeNull();
  });
});

describe("static popover strings", () => {
  it("resets at midnight UTC", () => {
    expect(USAGE_RESET_LABEL).toBe("00:00 UTC");
  });

  it("plans render with readable labels", () => {
    expect(planLabel("basic")).toBe("Basic");
    expect(planLabel("team")).toBe("Team");
    expect(planLabel("enterprise")).toBe("Enterprise");
    expect(planLabel(null)).toBe("Usage");
  });

  it("footer shows the actual clock time, not a relative string", () => {
    const time = formatUpdateTime(new Date(2026, 8, 24, 21, 8, 34).getTime());
    expect(time).toMatch(/9:08:34 PM/);
  });
});