"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { MotionIcon } from "motion-icons-react";
import { Inbox } from "lucide-react";
import { Eyebrow, PageHeading } from "@/components/views/Display";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { api, isFacebookUrl } from "@/lib/api";
import { useUsage } from "@/lib/hooks";
import type { JobSummary } from "@/lib/types";
import { cn, formatCompact, formatDateTime } from "@/lib/utils";

/**
 * Logged-in home dashboard: quick-launch a target into Investigation,
 * one-click relaunch chips from recent targets, the five most recent runs,
 * and today's usage telemetry (same source as the ops panel).
 */
export function DashboardView() {
  const router = useRouter();
  const { usage } = useUsage();
  const [url, setUrl] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [activeCount, setActiveCount] = useState<number | null>(null);
  const [jobsError, setJobsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Independent fetches: a failing active-count must never blank the
    // recent list (or vice versa).
    api
      .listJobs({ page: 1, page_size: 10 })
      .then((res) => {
        if (!cancelled) setJobs(res.items);
      })
      .catch((error) => {
        if (!cancelled)
          setJobsError(error instanceof Error ? error.message : "Could not load recent jobs");
      });
    api
      .listJobs({ status: "queued,running", page: 1, page_size: 1 })
      .then((res) => {
        if (!cancelled) setActiveCount(res.total);
      })
      .catch(() => {
        if (!cancelled) setActiveCount(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const recentTargets = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const job of jobs) {
      const first = job.urls[0];
      if (first && !seen.has(first)) {
        seen.add(first);
        out.push(first);
      }
      if (out.length >= 4) break;
    }
    return out;
  }, [jobs]);

  const launch = (raw?: string) => {
    const check = isFacebookUrl(raw ?? url);
    if (!check.valid || !check.normalized) {
      setValidationError(check.reason ?? "Enter a valid Facebook page or profile URL.");
      // Re-trigger the pill shake on every rejected submit.
      setShakeKey((value) => value + 1);
      return;
    }
    setValidationError(null);
    router.push(`/investigation?url=${encodeURIComponent(check.normalized)}`);
  };

  const [focused, setFocused] = useState(false);
  const [shakeKey, setShakeKey] = useState(0);
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (blurTimer.current) clearTimeout(blurTimer.current);
    };
  }, []);

  const sheetTargets =
    focused && recentTargets.length > 0
      ? recentTargets.filter((target) =>
          target.toLowerCase().includes(url.trim().toLowerCase())
        )
      : [];

  return (
    <div className="mx-auto w-full max-w-5xl px-8 pb-20">
      <header className="pb-8 pt-6">
        <Eyebrow>Overview</Eyebrow>
        <PageHeading className="font-semibold">Dashboard</PageHeading>
      </header>

      <section aria-label="Quick launch">
        <div
          key={shakeKey}
          className={cn(
            "flex w-full items-center gap-2 rounded-full border bg-card py-2 pl-6 pr-2 shadow-sm transition-all duration-200",
            shakeKey > 0 && validationError
              ? "animate-shake border-danger"
              : "border-border focus-within:border-accent",
          )}
        >
          <input
            type="text"
            value={url}
            onChange={(event) => {
              setUrl(event.target.value);
              if (validationError) setValidationError(null);
            }}
            onFocus={() => {
              if (blurTimer.current) clearTimeout(blurTimer.current);
              setFocused(true);
            }}
            onBlur={() => {
              blurTimer.current = setTimeout(() => setFocused(false), 120);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") launch();
              if (event.key === "Escape") setFocused(false);
            }}
            placeholder="Paste a Facebook URL…"
            aria-label="Target Facebook URL"
            autoComplete="off"
            spellCheck={false}
            className="min-w-0 flex-1 bg-transparent font-mono text-sm text-ink placeholder:text-ink-faint focus:outline-hidden"
          />
          <button
            type="button"
            onClick={() => launch()}
            aria-label="Launch investigation"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-accent text-accent-ink transition-transform duration-200 hover:scale-110 active:scale-95"
          >
            <MotionIcon name="Play" size={16} aria-hidden="true" animation="pop" trigger="hover" />
          </button>
        </div>

        <AnimatePresence initial={false}>
          {sheetTargets.length > 0 ? (
            <motion.div
              key="recent-sheet"
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
              className="mt-1.5 overflow-hidden rounded-xl border border-border bg-card shadow-lg"
            >
              <ul className="max-h-52 overflow-y-auto p-1.5" aria-label="Recent targets">
                {sheetTargets.map((target) => (
                  <li key={target}>
                    <button
                      type="button"
                      title={target}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => launch(target)}
                      className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-bg-subtle"
                    >
                      <MotionIcon name="History" size={13} aria-hidden="true" />
                      <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-muted">
                        {target.replace(/^https?:\/\/(www\.|m\.)?/, "").replace(/\/$/, "")}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </motion.div>
          ) : null}
        </AnimatePresence>

        {validationError ? (
          <p className="mt-2 text-xs text-danger" aria-live="polite">
            {validationError}
          </p>
        ) : (
          <div
            className="mt-4 grid max-w-4xl grid-cols-1 gap-3 sm:grid-cols-3"
            aria-label="Today's usage"
            aria-live="polite"
          >
            <div className="rounded-md border border-border bg-bg-subtle px-4 py-3">
              <p className="font-display text-xl leading-none tabular-nums text-ink">
                {activeCount == null ? <Skeleton className="h-6 w-8" /> : activeCount}
              </p>
              <p className="mt-1.5 text-xs font-medium text-ink-muted">Active runs</p>
            </div>
            <div className="rounded-md border border-border bg-bg-subtle px-4 py-3">
              <p className="font-display text-xl leading-none tabular-nums text-ink">
                {usage == null ? <Skeleton className="h-6 w-8" /> : usage.jobs_today.toLocaleString("en-US")}
              </p>
              <p className="mt-1.5 text-xs font-medium text-ink-muted">Jobs today</p>
            </div>
            <div className="rounded-md border border-border bg-bg-subtle px-4 py-3">
              <p className="font-display text-xl leading-none tabular-nums text-ink">
                {usage == null ? <Skeleton className="h-6 w-8" /> : formatCompact(usage.posts_today)}
              </p>
              <p className="mt-1.5 text-xs font-medium text-ink-muted">Posts today</p>
            </div>
          </div>
        )}
      </section>

      <section aria-label="Recent jobs" className="mt-8">
        <div className="flex items-baseline justify-between gap-4">
          <p className="text-xs font-medium text-ink-muted">
            Recent jobs
          </p>
          <Link
            href="/history"
            className="flex items-center gap-1 text-sm font-medium text-ink-muted transition-colors hover:text-ink"
          >
            View all
            <MotionIcon name="ArrowRight" size={13} aria-hidden="true" animation="nudge" trigger="hover" />
          </Link>
        </div>
        <div className="mt-1">
          {jobsError ? (
            <p className="px-1 py-6 text-sm text-danger">{jobsError}</p>
          ) : jobs.length === 0 ? (
            <EmptyState
              icon={<Inbox className="h-8 w-8" strokeWidth={1.5} aria-hidden="true" />}
              title="No runs yet"
              description="Paste a Facebook URL above to start your first crawl."
              action={
                <Link
                  href="/investigation"
                  className="inline-flex h-9 items-center justify-center rounded-sm border border-border-strong bg-bg-elevated px-4 text-sm font-medium text-ink transition-colors hover:bg-bg-subtle"
                >
                  Open the scraper
                </Link>
              }
            />
          ) : (
            <ul className="divide-y divide-border">
              {jobs.map((job) => {
                const live = job.status === "running" || job.status === "queued";
                return (
                  <li key={job.job_id}>
                    <Link
                      href={`/investigation?job=${encodeURIComponent(job.job_id)}`}
                      className="group flex items-center gap-3 px-1 py-3 transition-colors hover:bg-bg-subtle/60"
                    >
                      <span
                        aria-hidden="true"
                        className={cn(
                          "h-1.5 w-1.5 shrink-0 rounded-full",
                          live ? "bg-success animate-pulse" : job.status === "failed" ? "bg-danger" : "bg-ink-faint",
                        )}
                      />
                      <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-muted">
                        {job.urls[0] ?? job.job_id.slice(0, 8)}
                        {job.urls.length > 1 ? ` +${job.urls.length - 1}` : ""}
                      </span>
                      <span className="shrink-0 font-mono text-xs tabular-nums text-ink">
                        {formatCompact(job.posts_processed)} posts
                      </span>
                      <span
                        className="hidden shrink-0 font-mono text-[11px] text-ink-muted sm:block"
                        title={job.created_at ?? undefined}
                      >
                        {formatDateTime(job.created_at)}
                      </span>
                      <span className="shrink-0 rounded-full bg-bg-subtle px-2 py-0.5 text-xs font-medium text-ink-muted">
                        {job.status}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}
