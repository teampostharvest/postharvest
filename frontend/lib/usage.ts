/**
 * Usage-metric logic for the top-bar usage pill + popover (plans/usagecomp.md).
 *
 * Single source of the bounded/unbounded color rule:
 *  - a resource with a real ceiling (`max != null`) is colored by REMAINING
 *    headroom (>=50% healthy, <50% warning, 0 exhausted);
 *  - a resource with no ceiling (`max == null`) is always plain primary
 *    text — green on "unlimited" would imply a safety margin where no limit
 *    exists, which is exactly the inconsistency being fixed.
 *
 * Everything here is pure so the rule is unit-testable without a component.
 */
import { formatNumber } from "./utils";
import { PLAN_LABELS, type UsageResponse } from "./types";

export type UsageTone = "healthy" | "warning" | "exhausted" | "unbounded";

/**
 * Tone for a used/limit pair. `current` is the consumed amount, `max` the
 * ceiling (`null` = no ceiling). Tone derives from remaining headroom.
 */
export function usageTone(current: number, max: number | null | undefined): UsageTone {
  if (max == null) return "unbounded";
  const remaining = Math.max(0, max - current);
  if (remaining === 0) return "exhausted";
  return remaining / max >= 0.5 ? "healthy" : "warning";
}

/** Tailwind text utilities per tone (tokens defined in globals.css). */
export const USAGE_TONE_TEXT: Record<UsageTone, string> = {
  healthy: "text-usage-healthy",
  warning: "text-usage-warning",
  exhausted: "text-usage-exhausted",
  unbounded: "text-usage-unbounded",
};

export type UsageValueFormat = "of" | "slash" | "free";

export interface UsageValueParts {
  /** The current-count portion — the only part ever bolded. */
  head: string;
  /** The rest (denominator / unit) — never bolded, never the count. */
  tail: string;
  tone: UsageTone;
}

export function usageValue(
  current: number,
  max: number | null,
  format: UsageValueFormat,
): UsageValueParts {
  const maxText = max == null ? "unlimited" : formatNumber(max);
  const tone = usageTone(current, max);
  switch (format) {
    case "of":
      return { head: formatNumber(current), tail: ` of ${maxText}`, tone };
    case "slash":
      return { head: formatNumber(current), tail: `/${maxText}`, tone };
    case "free": {
      // "Pool availability" shows remaining slots: `max - current`, 0 floor.
      if (max == null) return { head: "unlimited", tail: " free", tone };
      return { head: formatNumber(Math.max(0, max - current)), tail: " free", tone };
    }
  }
}

export interface UsagePillParts {
  plan: string;
  pool: UsageValueParts;
  jobs: UsageValueParts;
}

/** Readable plan name for any server-normalized plan id. */
export function planLabel(plan: string | null | undefined): string {
  if (!plan) return "Usage";
  const known = PLAN_LABELS[plan];
  if (known) return known;
  return plan.charAt(0).toUpperCase() + plan.slice(1);
}

/** Pill body `{Plan} · Pool x/y · Jobs x/y` (pool = personal sessions, jobs = concurrent jobs). */
export function usagePillParts(usage: UsageResponse | null | undefined): UsagePillParts | null {
  if (!usage) return null;
  const pool = usage.personal_accounts;
  const jobs = usage.jobs_running;
  return {
    plan: planLabel(usage.plan),
    pool: usageValue(pool.used, pool.limit, "slash"),
    jobs: usageValue(jobs.used, jobs.limit, "slash"),
  };
}

/** Daily usage-period boundary. Constant by design (resets at midnight UTC). */
export const USAGE_RESET_LABEL = "00:00 UTC";

const updateTimeFmt = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
});

/** Actual clock time for the footer (never "just now" — this may be read long after load). */
export function formatUpdateTime(epochMs: number): string {
  return updateTimeFmt.format(new Date(epochMs));
}