"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { ApiErrorBanner } from "@/components/features/scraper/ApiErrorBanner";
import { ExportArea } from "@/components/features/posts/ExportArea";
import { KpiCards } from "@/components/features/metrics/KpiCards";
import { PostDetailDrawer } from "@/components/features/posts/PostDetailDrawer";
import { PostsTable } from "@/components/features/posts/PostsTable";
import { ProgressSection } from "@/components/features/scraper/ProgressSection";
import { UrlInputCard } from "@/components/features/scraper/UrlInputCard";
import { ApiError, api, isTerminalStatus } from "@/lib/api";
import { useJobPosts, useJobProgress } from "@/lib/hooks";
import type { Post, ScrapeRequest } from "@/lib/types";

const POLL_INTERVAL_MS = 1500;

function ScraperRunningHeading() {
  return <span>Scraper running</span>;
}

export default function InvestigationPage() {
  return (
    <Suspense fallback={null}>
      <InvestigationContent />
    </Suspense>
  );
}

function InvestigationContent() {
  const searchParams = useSearchParams();
  const urlParam = searchParams.get("url");
  const urlJobParam = searchParams.get("job");

  const [jobId, setJobId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [startError, setStartError] = useState<ApiError | null>(null);
  const [selectedPost, setSelectedPost] = useState<Post | null>(null);
  const [formDismissed, setFormDismissed] = useState(false);
  const lastRequestRef = useRef<ScrapeRequest | null>(null);
  const resultsRef = useRef<HTMLDivElement | null>(null);

  // History deep links /?job=... → /investigation?job=... reopen a past run.
  useEffect(() => {
    setJobId(urlJobParam);
    if (urlJobParam) setFormDismissed(true);
  }, [urlJobParam]);

  const { job, error: pollError, retry: retryPoll } = useJobProgress(jobId, { pollMs: POLL_INTERVAL_MS });
  const postsState = useJobPosts(job && isTerminalStatus(job.status) ? jobId : null);

  const jobActive = jobId !== null && job !== null && !isTerminalStatus(job.status);
  const jobTerminal = job !== null && isTerminalStatus(job.status);
  // Deep link (?job=) while the status is still loading: neither running
  // nor finished — hold the idle headline instead of flashing "running".
  const jobLoading = jobId !== null && job === null;

  // Scroll to results once the job finishes.
  useEffect(() => {
    if (jobTerminal) {
      resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [jobTerminal]);

  const handleStart = useCallback(async (request: ScrapeRequest) => {
    lastRequestRef.current = request;
    setSubmitting(true);
    setStartError(null);
    try {
      const response = await api.startScrape(request);
      setJobId(response.job_id);
      setSelectedPost(null);
      setFormDismissed(true);
    } catch (error) {
      setStartError(
        error instanceof ApiError ? error : new ApiError({ code: "network_error", message: "Failed to start the job." })
      );
    } finally {
      setSubmitting(false);
    }
  }, []);

  const handleReset = useCallback(() => {
    setJobId(null);
    setSelectedPost(null);
    setStartError(null);
  }, []);

  const retryStart = useCallback(() => {
    if (lastRequestRef.current) {
      void handleStart(lastRequestRef.current);
    }
  }, [handleStart]);

  return (
    <>
      <div className="mx-auto w-full max-w-5xl px-8 pb-20">
        <AnimatePresence mode="wait" initial={false}>
          {jobActive ? (
            <motion.h1
              key="running"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
              className="mb-8 pt-6 font-display text-2xl font-semibold tracking-tight text-ink"
            >
              <ScraperRunningHeading />
            </motion.h1>
          ) : jobTerminal ? (
            <motion.h1
              key="results"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
              className="mb-8 pt-6 font-display text-2xl font-semibold tracking-tight text-ink"
            >
              {job?.status === "failed" ? "Results (partial)" : "Results"}
            </motion.h1>
          ) : (
            <motion.h1
              key="idle"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
              className="mb-8 pt-6 font-display text-2xl font-semibold tracking-tight text-ink"
            >
              Target, configure, run.
            </motion.h1>
          )}
        </AnimatePresence>

        <AnimatePresence initial={false}>
          {!formDismissed ? (
            <motion.div
              key="url-input-card"
              exit={{ opacity: 0, height: 0, marginBottom: 0 }}
              transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
              className="overflow-hidden"
            >
              <UrlInputCard
                initialUrls={urlParam ?? undefined}
                disabled={jobActive}
                submitting={submitting}
                onSubmit={handleStart}
                onClearError={() => setStartError(null)}
              />
            </motion.div>
          ) : null}
        </AnimatePresence>

        {startError ? (
          <div className="mt-6">
            <ApiErrorBanner
              title="Could not start the job"
              message={startError.message}
              onRetry={retryStart}
              retryLabel="Try again"
            />
          </div>
        ) : null}

        {jobId && !jobTerminal ? (
          <div className="mt-10 border-t border-border pt-8">
            <ProgressSection active={jobActive || jobLoading} job={job} error={pollError} onRetry={retryPoll} />
          </div>
        ) : null}

        {jobTerminal ? (
          <div ref={resultsRef} className="mt-10 scroll-mt-24 space-y-6 border-t border-border pt-8" aria-live="polite">
            <p className="flex flex-wrap items-center gap-2 text-xs font-medium text-ink-muted">
              <span className="truncate">
                Job <code className="border border-border px-1.5 py-0.5 font-mono">{jobId}</code>
              </span>
              {job?.pages_total != null ? (
                <span className="rounded-full bg-bg-subtle px-2 py-0.5 font-mono text-xs tabular-nums">
                  {job.pages_total} page{job.pages_total === 1 ? "" : "s"}
                </span>
              ) : null}
            </p>

            <KpiCards
              posts={postsState.posts}
              total={postsState.total}
              capped={postsState.capped}
              loading={postsState.loading}
              error={postsState.error?.message ?? null}
              onRetry={postsState.reload}
            />

            <PostsTable
              posts={postsState.posts}
              total={postsState.total}
              loading={postsState.loading}
              loaded={postsState.loaded}
              error={postsState.error?.message ?? null}
              onRetry={postsState.reload}
              onSelectPost={setSelectedPost}
            />

            <ExportArea jobId={jobId} status={job?.status ?? null} onNewScrape={handleReset} />
          </div>
        ) : null}
      </div>

      <PostDetailDrawer post={selectedPost} open={selectedPost !== null} onClose={() => setSelectedPost(null)} />
    </>
  );
}