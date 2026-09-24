"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ArrowRight, RefreshCw, Search } from "lucide-react";
import { Eyebrow, PageHeading } from "@/components/views/Display";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { JobSummary } from "@/lib/types";
import { formatCompact, formatDateTime } from "@/lib/utils";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 25;

function StatusBadge({ status }: { status: JobSummary["status"] }) {
  const runningOrQueued = status === "running" || status === "queued";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        status === "failed"
          ? "border-danger/40 bg-danger/10 text-danger"
          : runningOrQueued
            ? "border-accent/40 bg-accent/10 text-accent"
            : "border-border text-ink-muted",
      )}
    >
      {runningOrQueued ? <span className="h-1 w-1 rounded-full bg-accent animate-pulse-dot" aria-hidden="true" /> : null}
      {status}
    </span>
  );
}

export default function HistoryPage() {
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<JobSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listJobs({ page, page_size: PAGE_SIZE });
      setItems(res.items);
      setTotal(res.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load job history");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((job) => {
      const haystack = [job.job_id, ...job.urls].join(" ").toLowerCase();
      return haystack.includes(q);
    });
  }, [items, query]);

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-6">
      <header className="border-b border-border pb-8">
        <Eyebrow>{`${total} run${total === 1 ? "" : "s"} stored`}</Eyebrow>
        <PageHeading>History</PageHeading>
        <p className="max-w-2xl text-sm leading-relaxed text-ink-muted sm:text-base">
          Every scrape ever started, in order. Reopen a finished run for its full results and exports, or
          watch a live one land.
        </p>
      </header>

      <section className="rounded-md border border-border-strong bg-bg-elevated shadow-sm" aria-label="Runs history">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="relative w-full sm:max-w-xs">
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search id or URL…"
              aria-label="Search runs"
              className="h-8 font-mono text-xs"
            />
          </div>
          {error ? (
            <button
              type="button"
              onClick={() => void load()}
              className="flex items-center gap-1.5 border border-border-strong px-2 py-1 text-xs font-medium text-ink-muted transition-colors hover:text-ink"
            >
              <RefreshCw className="h-3 w-3" strokeWidth={1.75} /> Retry
            </button>
          ) : (
            <span className="font-mono text-xs tabular-nums text-ink-muted">
              page {page} / {pages}
            </span>
          )}
        </div>

        {error ? (
          <p className="px-4 py-6 text-sm text-danger">{error}</p>
        ) : loading && items.length === 0 ? (
          <div className="px-4 py-6" role="status" aria-label="Loading runs">
            <div className="space-y-1">
              {Array.from({ length: 5 }).map((_, index) => (
                <div key={index} className="grid grid-cols-12 items-center gap-2 px-4 py-3">
                  <Skeleton className="h-4 w-16" />
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="col-span-3 h-4 w-full" />
                  <Skeleton className="h-4 w-8" />
                  <Skeleton className="h-4 w-8" />
                  <Skeleton className="h-4 w-16" />
                  <Skeleton className="h-4 w-10 justify-self-end" />
                </div>
              ))}
            </div>
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={<Search strokeWidth={1.5} className="h-10 w-10" />}
            title={query ? "No runs match your search" : "No runs yet"}
            description={
              query
                ? "Try a different id or URL — or clear the search to browse everything."
                : "Your scraping history will show up here once you start a job."
            }
            action={
              query ? undefined : (
                <Link
                  href="/investigation"
                  aria-label="Start a new scrape"
                  className="inline-flex items-center gap-1.5 rounded-sm border border-border-strong bg-bg-elevated px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-bg-subtle"
                >
                  Start a new scrape <ArrowRight className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                </Link>
              )
            }
          />
        ) : (
          <>
            <div className="hidden grid-cols-12 gap-2 border-b border-border bg-bg-subtle px-4 py-2 text-xs font-medium text-ink-muted sm:grid">
              <span className="col-span-2">Id</span>
              <span className="col-span-2">When</span>
              <span className="col-span-3">Sources</span>
              <span className="col-span-1">Posts</span>
              <span className="col-span-1">Pages</span>
              <span className="col-span-2">Status</span>
              <span className="col-span-1 text-right">Open</span>
            </div>

            <ul className="divide-y divide-border">
              {filtered.map((job) => (
                <li
                  key={job.job_id}
                  className="group grid grid-cols-12 items-center gap-2 px-4 py-3 transition-colors hover:bg-bg-subtle/60"
                >
                  <span className="col-span-2 truncate font-mono text-xs text-ink-muted">{job.job_id.slice(0, 8)}</span>
                  <span className="col-span-2 truncate font-mono text-xs text-ink-muted" title={job.created_at ?? undefined}>
                    {formatDateTime(job.created_at)}
                  </span>
                  <span className="col-span-3 truncate font-mono text-xs text-ink-muted" title={job.urls.join("\n")}>
                    {job.urls.length > 0 ? job.urls[0] : "—"}
                    {job.urls.length > 1 ? ` +${job.urls.length - 1}` : ""}
                  </span>
                  <span className="col-span-1 font-mono text-xs tabular-nums text-ink">
                    {formatCompact(job.posts_processed)}
                    {job.posts_found !== job.posts_processed ? (
                      <span className="text-ink-muted">/{formatCompact(job.posts_found)}</span>
                    ) : null}
                  </span>
                  <span className="col-span-1 font-mono text-xs tabular-nums text-ink-muted">
                    {formatCompact(job.pages_completed)}/{formatCompact(job.pages_total)}
                  </span>
                  <span className="col-span-2">
                    <StatusBadge status={job.status} />
                  </span>
                  <span className="col-span-1 text-right">
                    <Link
                      href={`/investigation?job=${job.job_id}`}
                      aria-label={`Open run ${job.job_id}`}
                      className="inline-flex items-center gap-1 rounded-sm border border-transparent px-2 py-1 text-xs font-medium text-ink-muted transition-colors hover:border-accent hover:text-ink sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100"
                    >
                      Open <ArrowRight className="h-3 w-3" strokeWidth={1.75} />
                    </Link>
                  </span>
                </li>
              ))}
            </ul>

            <div className="flex items-center justify-between border-t border-border px-4 py-3">
              <span className="font-mono text-xs tabular-nums text-ink-muted">
                {total} total
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  <ArrowLeft className="h-3 w-3" strokeWidth={1.75} /> Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= pages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next <ArrowRight className="h-3 w-3" strokeWidth={1.75} />
                </Button>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}