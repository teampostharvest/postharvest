"use client";

import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Eyebrow, PageHeading } from "@/components/views/Display";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { type PlanLimits, type PlanName } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Pricing tiers.
 *
 * The enforceable numbers (URLs per job, posts per source, concurrent jobs,
 * saved accounts) are fetched from GET /api/plans, which reads
 * backend/core/plans.py (PLAN_LIMITS) — the canonical source enforced
 * server-side. Rows a tier cannot express as a cap (price, team users,
 * support) stay marketing-facing here. FALLBACK_LIMITS mirrors the backend so
 * the page still renders if the catalog is briefly unreachable.
 *
 * Selection is self-serve: PATCH /api/auth/me/plan applies the chosen tier to
 * the caller's next job.
 */
interface Tier {
  id: PlanName;
  name: string;
  price: string;
  tagline: string;
  extras: Array<{ label: string; value: string }>;
}

const TIERS: Tier[] = [
  {
    id: "basic",
    name: "Basic",
    price: "Free",
    tagline: "A single focused crawl — run the full pipeline end to end.",
    extras: [
      { label: "Team users", value: "1" },
      { label: "Support", value: "Community" },
    ],
  },
  {
    id: "pro",
    name: "Pro",
    price: "$19/mo",
    tagline: "Serious investigation throughput with room to parallelise.",
    extras: [
      { label: "Team users", value: "3" },
      { label: "Support", value: "Priority" },
    ],
  },
  {
    id: "team",
    name: "Team",
    price: "$99/mo",
    tagline: "Shared capacity for small teams running deep, regular crawls.",
    extras: [
      { label: "Team users", value: "10" },
      { label: "Support", value: "Priority" },
    ],
  },
  {
    id: "enterprise",
    name: "Enterprise",
    price: "$190/mo",
    tagline: "Bulk research at scale — no per-account ceiling.",
    extras: [
      { label: "Team users", value: "Unlimited" },
      { label: "Support", value: "Dedicated / Slack" },
    ],
  },
];

/** Mirrors backend/core/plans.py PLAN_LIMITS (used until the catalog loads). */
export const FALLBACK_LIMITS: Record<PlanName, PlanLimits> = {
  basic: { urls: 5, max_posts: 500, concurrent_jobs: 1, personal_accounts: 1 },
  pro: { urls: 50, max_posts: 5_000, concurrent_jobs: 3, personal_accounts: 5 },
  team: { urls: 150, max_posts: 100_000, concurrent_jobs: 10, personal_accounts: 25 },
  enterprise: { urls: null, max_posts: null, concurrent_jobs: 10, personal_accounts: null },
};

const SHARED_FEATURES = [
  "HTTP and browser (GraphQL) crawl engines",
  "Live login capture for saved sessions",
  "JSON + XLSX export with honest counts",
  "Per-owner isolation plus ops shared accounts",
];

function limitRows(limits: PlanLimits): Array<{ label: string; value: string }> {
  const format = (value: number | null): string =>
    value == null ? "Unlimited" : value.toLocaleString("en-US");
  return [
    { label: "URLs per job", value: format(limits.urls) },
    { label: "Posts per source", value: format(limits.max_posts) },
    { label: "Concurrent jobs", value: format(limits.concurrent_jobs) },
    { label: "Saved accounts", value: format(limits.personal_accounts) },
  ];
}

export default function PricingPage() {
  const { profile } = useAuth();
  const currentPlan = (profile?.plan ?? "basic") as PlanName;

  const [limits, setLimits] = useState<Record<PlanName, PlanLimits>>(FALLBACK_LIMITS);

  useEffect(() => {
    let cancelled = false;
    api
      .listPlans()
      .then((catalog) => {
        if (cancelled) return;
        const next: Record<PlanName, PlanLimits> = { ...FALLBACK_LIMITS };
        for (const entry of catalog) {
          next[entry.id] = entry.limits;
        }
        setLimits(next);
      })
      .catch(() => {
        // The catalog is authoritative but non-critical — keep mirrored defaults.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="w-full">
      <header className="mx-auto w-full max-w-6xl">
        <Eyebrow>Plans</Eyebrow>
        <PageHeading>Pricing</PageHeading>
        <p className="-mt-4 mb-6 max-w-xl text-sm leading-relaxed text-ink-muted">
          Four honest tiers. Limits are enforced server-side on every job, so
          the numbers here are the numbers you get.
        </p>
      </header>

      <section
        className="mx-auto grid w-full max-w-6xl grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4"
        aria-label="Plans"
      >
        {TIERS.map((tier) => {
          const isCurrent = tier.id === currentPlan;
          const rows = [...limitRows(limits[tier.id]), ...tier.extras];
          return (
            <article
              key={tier.id}
              className={cn(
                "flex flex-col rounded-md border bg-card",
                isCurrent ? "border-accent shadow-sm" : tier.id === "pro" ? "border-accent/40" : "border-border",
              )}
            >
              <div className="p-6 pb-0">
                <div className="flex items-center justify-between">
                  <h2 className="text-lg font-semibold tracking-tight text-ink">
                    {tier.name}
                  </h2>
                  {isCurrent ? (
                    <Badge variant="default" className="border-transparent bg-accent text-accent-ink">
                      Current plan
                    </Badge>
                  ) : tier.id === "pro" ? (
                    <Badge variant="default" className="border-transparent bg-accent/15 text-accent">
                      Most popular
                    </Badge>
                  ) : null}
                </div>
                <p className="mt-2 text-sm leading-relaxed text-ink-muted">
                  {tier.tagline}
                </p>
                <p className="mt-4 font-display text-2xl tabular-nums tracking-tight text-ink">
                  {tier.price}
                </p>
              </div>

              <dl className="mt-6 flex-1">
                {rows.map((row) => (
                  <div
                    key={row.label}
                    className="flex flex-col gap-1 border-t border-border px-6 py-3"
                  >
                    <dt className="text-xs font-medium text-ink-muted">
                      {row.label}
                    </dt>
                    <dd className="font-mono text-xs tabular-nums text-ink">
                      {row.value}
                    </dd>
                  </div>
                ))}
              </dl>

              <div className="border-t border-border p-6">
                {isCurrent ? (
                  <span className="inline-flex h-9 w-full items-center justify-center" aria-hidden="true" />
                ) : (
                  <a
                    href="mailto:hello@postharvest.space?subject=PostHarvest%20plan"
                    className="inline-flex h-9 w-full items-center justify-center whitespace-nowrap rounded-sm border border-border-strong bg-bg-elevated px-4 text-sm font-medium text-ink transition-colors hover:bg-bg-subtle"
                  >
                    Talk to sales
                  </a>
                )}
              </div>
            </article>
          );
        })}
      </section>

      <section
        className="mx-auto mt-14 w-full max-w-6xl border-t border-border pt-8"
        aria-label="Included in every plan"
      >
        <ol className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {SHARED_FEATURES.map((feature) => (
            <li key={feature} className="flex items-start gap-2.5">
              <Check
                className="mt-0.5 h-4 w-4 shrink-0 text-highlight"
                strokeWidth={1.75}
                aria-hidden="true"
              />
              <span className="text-sm leading-relaxed text-ink-muted">
                {feature}
              </span>
            </li>
          ))}
        </ol>
        <p className="mt-8 text-xs leading-relaxed text-ink-muted">
          Switching tiers is self-serve and applies to your next job.
        </p>
      </section>
    </div>
  );
}
