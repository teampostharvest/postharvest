"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, FileX2, MapPin, RefreshCcw } from "lucide-react";
import { MotionIcon } from "motion-icons-react";
import { ApiErrorBanner } from "@/components/features/scraper/ApiErrorBanner";
import { Button } from "@/components/ui/button";
import { safeHttpUrl, type ApiError } from "@/lib/api";
import type { JobErrorDetail, JobProgress, SourceProgress } from "@/lib/types";
import { cn, formatNumber } from "@/lib/utils";

export interface ProgressSectionProps {
  active: boolean;
  job: JobProgress | null;
  error: ApiError | null;
  onRetry: () => void;
}

function clampPercent(value: number): number {
  return Math.max(0, Math.min(100, value));
}

/** Exported for tests; see decision log D13. */
export function computePercent(job: JobProgress): number | null {
  const pagesTotal = job.pages_total ?? 0;
  const pagesDone = job.pages_completed ?? 0;
  const postsFound = job.posts_found ?? 0;
  const postsProcessed = job.posts_processed ?? 0;
  const maxPosts = job.max_posts ?? 0;

  if (job.status === "completed") return 100;

  // Multi-source: page stepping is the authoritative signal; we have no
  // per-source live counters, so stay coarse rather than blend aggregates.
  if (pagesTotal > 1) {
    return clampPercent(Math.round((pagesDone / pagesTotal) * 100));
  }

  if (job.status === "failed") {
    if (postsFound > 0 && postsProcessed > 0) {
      return clampPercent(Math.round((postsProcessed / postsFound) * 100));
    }
    return 0;
  }

  // Single source: live post counters give a smooth, truthful signal once the
  // scraper starts streaming them (the fetcher processing loop or the browser
  // post-parse phase). posts_extracted is an absolute cumulative counter, so
  // processed/found is a genuine fraction of the work done.
  if (pagesTotal === 1) {
    if (pagesDone >= 1) return 100;
    if (postsFound > 0 && postsProcessed > 0) {
      return clampPercent(Math.round((postsProcessed / postsFound) * 100));
    }
    // Discovery heuristic: while the browser/HTTP fetcher is still finding
    // posts (posts_processed is 0), posts_found/max_posts is the only live
    // signal. This keeps the bar climbing during discovery instead of sticking
    // on an indeterminate shimmer.
    if (postsFound > 0 && maxPosts > 0) {
      return clampPercent(Math.round((postsFound / maxPosts) * 100));
    }
    // Still discovering and no cap to compare against: show the indeterminate
    // bar rather than a fake 0%.
    return null;
  }

  if (postsFound > 0 && postsProcessed > 0) {
    return clampPercent(Math.round((postsProcessed / postsFound) * 100));
  }
  return null;
}

/**
 * Seconds the job has actively been running, free of paused time.
 * Seeds from the server `started_at` once, then ticks forward only while the
 * job status is "running" (so a paused job freezes its ETA, Q14-A).
 */
function useActiveSeconds(job: JobProgress | null): number {
  const [activeSeconds, setActiveSeconds] = useState(0);
  const stateRef = useRef<{
    jobId: string | null;
    activeMs: number;
    lastTickTime: number;
  }>({ jobId: null, activeMs: 0, lastTickTime: 0 });

  useEffect(() => {
    const s = stateRef.current;
    const jobId = job?.job_id ?? null;
    if (jobId === s.jobId) return;
    s.jobId = jobId;
    s.activeMs = 0;
    setActiveSeconds(0);
    if (job?.started_at) {
      const t = Date.parse(job.started_at);
      if (!Number.isNaN(t)) {
        // Mid-run page load: approximate from the server start timestamp.
        s.activeMs = Math.max(0, Date.now() - t);
        setActiveSeconds(s.activeMs / 1000);
      }
    }
  }, [job?.job_id, job?.started_at]);

  useEffect(() => {
    const s = stateRef.current;
    if (job?.status !== "running" || s.jobId === null) {
      s.lastTickTime = 0;
      return;
    }
    s.lastTickTime = Date.now();
    const id = setInterval(() => {
      const now = Date.now();
      s.activeMs += Math.max(0, now - s.lastTickTime);
      s.lastTickTime = now;
      setActiveSeconds(s.activeMs / 1000);
    }, 1000);
    return () => clearInterval(id);
  }, [job?.status, job?.job_id]);

  return activeSeconds;
}

/** Exported for tests. Uses the posts_processed rate against the remaining
 * count (posts_found - posts_processed). During pure discovery (nothing
 * extracted yet) it falls back to posts_found vs max_posts so the ETA can
 * exist whenever a percentage does. */
export function computeEtaSeconds(activeSeconds: number, job: JobProgress): number | null {
  if (activeSeconds <= 0) return null;
  const postsFound = job.posts_found ?? 0;
  const postsProcessed = job.posts_processed ?? 0;
  const maxPosts = job.max_posts ?? 0;

  if (postsFound > 0 && postsProcessed > 0) {
    const rate = postsProcessed / activeSeconds;
    const remaining = postsFound - postsProcessed;
    if (rate <= 0 || remaining <= 0) return null;
    return remaining / rate;
  }
  if (postsFound > 0 && maxPosts > 0) {
    const rate = postsFound / activeSeconds;
    const remaining = maxPosts - postsFound;
    if (rate <= 0 || remaining <= 0) return null;
    return remaining / rate;
  }
  return null;
}

/** Q7-A: render ETA as minutes and seconds (e.g. "3m 24s"). Exported for tests. */
export function formatEta(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  return `${minutes}m ${secs}s`;
}

const SOURCE_STATUS_STYLES: Record<string, string> = {
  queued: "bg-ink-faint/60",
  running: "bg-accent animate-pulse",
  completed: "bg-ink",
  failed: "bg-danger",
  cancelled: "bg-ink-faint/60",
};

function SourceRow({ source }: { source: SourceProgress }) {
  const dotClass = SOURCE_STATUS_STYLES[source.status] ?? "bg-ink-faint/60";
  const label = source.status.length > 0 ? source.status.charAt(0).toUpperCase() + source.status.slice(1) : "Queued";
  return (
    <li className="flex items-center gap-2 py-0.5">
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", dotClass)} aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate text-sm text-ink-muted" title={source.url}>
        {source.url}
      </span>
      <span className="shrink-0 text-xs font-medium text-ink-muted">{label}</span>
      <span className="shrink-0 font-mono text-xs tabular-nums text-ink-muted">
        {formatNumber(source.posts_processed)} / {formatNumber(source.posts_found)}
      </span>
    </li>
  );
}

function ErrorListItem({ entry }: { entry: JobErrorDetail }) {
  const link = safeHttpUrl(entry.url ?? entry.post_url);
  return (
    <li className="border border-border bg-bg-subtle/50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-sm border border-danger/40 bg-danger/10 px-1.5 py-0.5 font-mono text-xs text-danger">
          {entry.code}
        </span>
        <span className="min-w-0 flex-1 wrap-break-word text-sm text-ink">{entry.message}</span>
      </div>
      {link ? (
        <p className="mt-1 truncate text-xs text-ink-muted">
          <MapPin className="mr-1 inline h-3 w-3" aria-hidden="true" />
          {entry.post_url ?? entry.url}
        </p>
      ) : null}
    </li>
  );
}

export function ProgressSection({ active, job, error, onRetry }: ProgressSectionProps) {
  const percent = useMemo(() => (job ? computePercent(job) : null), [job]);
  const activeSeconds = useActiveSeconds(job);
  const etaSeconds = useMemo(() => {
    if (!job || percent == null) return null;
    if (job.status !== "running" && job.status !== "paused") return null;
    // Q16-A: delay the ETA a few seconds so early rates don't produce noise.
    if (activeSeconds < 5) return null;
    return computeEtaSeconds(activeSeconds, job);
  }, [job, percent, activeSeconds]);
  const showUnreachable = error !== null && job === null;
  const showConnectionLoss = error !== null && job !== null;
  const failed = job?.status === "failed";
  const queued = job?.status === "queued";

  const stats = useMemo(() => {
    if (!job) return [];
    const rows: Array<{ label: string; value: string; danger?: boolean }> = [];
    if (job.pages_total != null) {
      rows.push({ label: "Pages", value: `${formatNumber(job.pages_completed)} / ${formatNumber(job.pages_total)}` });
    }
    rows.push({ label: "Posts found", value: formatNumber(job.posts_found) ?? "—" });
    rows.push({ label: "Posts processed", value: formatNumber(job.posts_processed) ?? "—" });
    rows.push({ label: "Duplicates", value: formatNumber(job.duplicates) ?? "—" });
    rows.push({ label: "Errors", value: formatNumber(job.errors) ?? "—", danger: (job.errors ?? 0) > 0 });
    return rows;
  }, [job]);

  const title = !job
    ? "Loading run"
    : failed
      ? "Scraping failed"
      : queued
        ? "Job queued"
        : job.status === "completed"
          ? "Run complete"
          : "Scraping in progress";
  const sources = job?.sources ?? [];

  const bar = percent != null ? (
    <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label="Job progress" className="h-1.5 w-full overflow-hidden rounded-full bg-bg-subtle">
      <div className={cn("h-full rounded-full transition-[width] duration-500 ease-out", failed ? "bg-danger" : "bg-ink progress-stripes")} style={{ width: `${percent}%` }} />
    </div>
  ) : (
    <div role="progressbar" aria-label="Job progress" className="h-1.5 w-full overflow-hidden rounded-full bg-bg-subtle">
      <div className="h-full w-1/3 animate-indeterminate rounded-full bg-ink" />
    </div>
  );

  return (
    <>
      {showUnreachable ? (
        <ApiErrorBanner variant="warning" title="API unreachable" message={`${error?.message ?? "Cannot reach the backend."} The job keeps running server-side; reconnecting will resume updates.`} onRetry={onRetry} retryLabel="Retry now" />
      ) : null}

      <section className="overflow-hidden rounded-xl border border-border bg-card shadow-sm" aria-label="Scraping monitor">
        <div className="px-5 pt-4">{bar}</div>

        <div className="flex items-center justify-between gap-4 px-5 pb-4 pt-3">
          <div className="flex min-w-0 items-center gap-2.5">
            {failed ? (
              <AlertTriangle className="h-4 w-4 shrink-0 text-danger" aria-hidden="true" />
            ) : (
              <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
                <span className="absolute h-full w-full rounded-full bg-accent opacity-60 animate-pulse-dot" />
                <span className="h-2 w-2 rounded-full bg-accent" />
              </span>
            )}
            <span className="truncate text-sm font-medium text-ink">{title}</span>
            {job?.job_id ? (
              <span className="shrink-0 rounded-full bg-bg-subtle px-2 py-0.5 font-mono text-xs tabular-nums text-ink-muted">
                job {job.job_id.slice(0, 8)}
              </span>
            ) : null}
            {showConnectionLoss ? (
              <span className="shrink-0 whitespace-nowrap text-xs font-medium text-warning">
                Connection lost, retrying…
              </span>
            ) : null}
          </div>

          <div className="flex shrink-0 items-center gap-3">
            {active && !showConnectionLoss && job?.status === "running" ? (
              <span className="flex items-center gap-1.5 rounded-full bg-success/10 px-2 py-0.5 text-xs font-medium text-success">
                <span className="h-1 w-1 rounded-full bg-success animate-pulse" aria-hidden="true" />
                Live
              </span>
            ) : null}
            {etaSeconds != null ? (
              // Q13-A: ETA sits immediately to the left of the percentage.
              <span className="font-mono text-xs tabular-nums text-ink-muted" title="Estimated time remaining">
                {formatEta(etaSeconds)}
              </span>
            ) : null}
            {percent != null ? (
              <span className={cn("font-mono text-2xl font-semibold tabular-nums leading-none", failed ? "text-danger" : "text-ink")}>
                {percent}%
              </span>
            ) : job == null ? (
              <span className="text-xs text-ink-muted">Loading</span>
            ) : queued ? (
              <span className="text-xs font-medium text-ink-muted">Queued</span>
            ) : (
              <span className="text-xs text-ink-muted">Starting</span>
            )}
          </div>
        </div>

        {sources.length > 0 ? (
          // Q4-A / Q12-A: links box on top of the progress section, inline and scrollable.
          <div className="border-t border-border px-5 py-3" aria-label="Links being scraped">
            <div className="mb-1.5 flex items-center gap-2">
              <MotionIcon name="Link2" size={13} aria-hidden="true" />
              <span className="text-xs font-medium text-ink-muted">Links being scraped</span>
              <span className="rounded-full bg-bg-subtle px-1.5 py-px font-mono text-xs tabular-nums text-ink-muted">
                {formatNumber(sources.length)}
              </span>
            </div>
            <ul className="max-h-28 space-y-1 overflow-y-auto pr-1">
              {sources.map((source) => (
                <SourceRow key={source.url} source={source} />
              ))}
            </ul>
          </div>
        ) : null}

        {stats.length > 0 ? (
          <div className="flex flex-wrap items-stretch divide-x divide-border border-t border-border bg-bg-subtle/40">
            {stats.map((stat) => (
              <div key={stat.label} className="flex min-w-[84px] flex-1 flex-col items-center justify-center gap-1 px-4 py-3">
                <span className={cn("font-mono text-lg font-semibold tabular-nums leading-none", stat.danger ? "text-danger" : "text-ink")}>
                  {stat.value}
                </span>
                <span className="whitespace-nowrap text-xs text-ink-muted">{stat.label}</span>
              </div>
            ))}
            <div className="flex min-w-[80px] flex-col items-center justify-center gap-1 px-4 py-3">
              <MotionIcon
                name={active ? "LoaderCircle" : "Clock"}
                size={15}
                aria-hidden="true"
                animation={active ? "spin" : "none"}
                trigger={active ? "always" : "hover"}
                className="text-ink-muted"
              />
              <span className="whitespace-nowrap text-xs text-ink-muted">
                {active ? "Inputs locked" : "Waiting"}
              </span>
            </div>
          </div>
        ) : null}

        {failed && job?.error_details && job.error_details.length > 0 ? (
          <div className="space-y-2 border-t border-border px-5 py-4">
            <div className="flex items-center justify-between gap-3">
              <h4 className="text-xs font-semibold text-ink">
                <FileX2 className="mr-1.5 inline h-3.5 w-3.5 text-danger" aria-hidden="true" />
                Error details ({job.error_details.length})
              </h4>
              <Button variant="outline" size="sm" onClick={onRetry} className="rounded-none border-border bg-transparent text-ink-muted hover:bg-bg-subtle hover:text-ink">
                <RefreshCcw className="mr-1.5 h-3 w-3" aria-hidden="true" /> Refresh
              </Button>
            </div>
            <ul className="max-h-40 space-y-2 overflow-y-auto">
              {job.error_details.map((entry, index) => (
                <ErrorListItem key={`${entry.code}-${index}`} entry={entry} />
              ))}
            </ul>
          </div>
        ) : null}
      </section>
    </>
  );
}