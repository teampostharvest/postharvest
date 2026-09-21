"use client";

/**
 * Client-side data hooks used by the dashboard. Keeping them here means the
 * page component stays declarative and the polling/fetch logic is testable.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, isTerminalStatus } from "./api";
import type { AccountsResponse, JobProgress, JobSummary, Post, UsageResponse } from "./types";

export interface JobProgressState {
  job: JobProgress | null;
  error: ApiError | null;
  retry: () => void;
}

/**
 * Polls GET /api/jobs/{id} every `pollMs` until the job reaches a terminal
 * state (completed/failed). On network failure the poll backs off but keeps
 * trying; `retry()` forces an immediate poll.
 */
export function useJobProgress(jobId: string | null, options: { pollMs?: number } = {}): JobProgressState {
  const pollMs = options.pollMs ?? 1500;
  const [job, setJob] = useState<JobProgress | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);
  const lastJobIdRef = useRef<string | null>(null);

  useEffect(() => {
    // Reset immediately when the target job changes (avoids flashing stale progress).
    if (jobId !== lastJobIdRef.current) {
      lastJobIdRef.current = jobId;
      setJob(null);
      setError(null);
    }
    if (!jobId) {
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async (): Promise<void> => {
      try {
        const next = await api.getJob(jobId);
        if (cancelled) return;
        setJob(next);
        setError(null);
        if (isTerminalStatus(next.status)) return; // done polling
        timer = setTimeout(() => {
          void tick();
        }, pollMs);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err : new ApiError({ code: "network_error", message: "Failed to reach the API." }));
        timer = setTimeout(() => {
          void tick();
        }, Math.min(pollMs * 2, 8000));
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, pollMs, attempt]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  return { job, error, retry };
}

export interface UsageState {
  usage: UsageResponse | null;
  /** Last failure (null while healthy). Polling continues so recovery is automatic. */
  error: ApiError | null;
  reload: () => void;
}

function useUsageFetch(enabled: boolean, pollMs: number): UsageState {
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setUsage(null);
      setError(null);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async (): Promise<void> => {
      try {
        const next = await api.getUsage();
        if (cancelled) return;
        setUsage(next);
        setError(null);
      } catch (err) {
        // 401/5xx/network/timeout — keep the last good snapshot (or nothing)
        // but surface the failure so panels offer a retry instead of a
        // permanent loader.
        if (cancelled) return;
        setError(err instanceof ApiError ? err : new ApiError({ code: "network_error", message: "Failed to reach the API." }));
      }
      timer = setTimeout(() => {
        void tick();
      }, pollMs);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [enabled, pollMs, attempt]);

  return { usage, error, reload: useCallback(() => setAttempt((value) => value + 1), []) };
}

export interface JobPostsState {
  posts: Post[];
  total: number;
  capped: boolean;
  loading: boolean;
  error: ApiError | null;
  loaded: boolean;
  reload: () => void;
}

/**
 * Polls GET /api/usage for the sidebar quota panel. Pass `enabled=false`
 * (e.g. signed-out) to skip fetching entirely.
 */
export function useUsage(options: { pollMs?: number; enabled?: boolean } = {}): UsageState {
  const { pollMs = 15000, enabled = true } = options;
  return useUsageFetch(enabled, pollMs);
}

export interface ActiveJobsState {
  jobs: JobSummary[];
  error: ApiError | null;
  reload: () => void;
}

/**
 * Polls the active (queued + running) jobs for the ops panel. Server-side
 * status filter keeps the payload small; failures keep the last snapshot
 * while surfacing the error for a retry affordance.
 */
export function useActiveJobs(options: { pollMs?: number; enabled?: boolean } = {}): ActiveJobsState {
  const { pollMs = 5000, enabled = true } = options;
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setJobs([]);
      setError(null);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async (): Promise<void> => {
      try {
        const next = await api.listJobs({ status: "queued,running", page: 1, page_size: 25 });
        if (cancelled) return;
        setJobs(next.items);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err : new ApiError({ code: "network_error", message: "Failed to reach the API." }));
      }
      timer = setTimeout(() => {
        void tick();
      }, pollMs);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [enabled, pollMs, attempt]);

  return { jobs, error, reload: useCallback(() => setAttempt((value) => value + 1), []) };
}

export interface AccountsState {
  accounts: AccountsResponse | null;
  error: ApiError | null;
  reload: () => void;
}

/**
 * Polls GET /api/accounts for the ops-panel quick view. Failures keep the
 * last snapshot while surfacing the error for a retry affordance;
 * management (add/remove) stays on the Saved accounts page.
 */
export function useAccounts(options: { pollMs?: number; enabled?: boolean } = {}): AccountsState {
  const { pollMs = 30000, enabled = true } = options;
  const [accounts, setAccounts] = useState<AccountsResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setAccounts(null);
      setError(null);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async (): Promise<void> => {
      try {
        const next = await api.listAccounts();
        if (cancelled) return;
        setAccounts(next);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err : new ApiError({ code: "network_error", message: "Failed to reach the API." }));
      }
      timer = setTimeout(() => {
        void tick();
      }, pollMs);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [enabled, pollMs, attempt]);

  return { accounts, error, reload: useCallback(() => setAttempt((value) => value + 1), []) };
}

/**
 * Loads all posts for a finished job by walking the paginated endpoint.
 * A hard cap prevents unbounded memory use (spec §15); the UI shows a note
 * when the dataset is larger than the cap.
 */
export function useJobPosts(
  jobId: string | null,
  options: { pageSize?: number; maxPosts?: number } = {}
): JobPostsState {
  const { pageSize = 200, maxPosts = 2000 } = options;
  const [state, setState] = useState<Omit<JobPostsState, "reload">>({
    posts: [],
    total: 0,
    capped: false,
    loading: false,
    error: null,
    loaded: false,
  });
  const [attempt, setAttempt] = useState(0);
  const lastJobIdRef = useRef<string | null>(null);

  useEffect(() => {
    // Reset results when switching to a different job.
    if (jobId !== lastJobIdRef.current) {
      lastJobIdRef.current = jobId;
      setState({ posts: [], total: 0, capped: false, loading: false, error: null, loaded: false });
    }
    if (!jobId) {
      return;
    }

    let cancelled = false;
    setState((previous) => ({ ...previous, loading: true, error: null }));

    (async () => {
      try {
        const all: Post[] = [];
        let page = 1;
        let total = 0;
        let capped = false;

        for (;;) {
          const result = await api.getPosts(jobId, { page, page_size: pageSize });
          total = result.total;
          all.push(...result.items);
          if (all.length >= maxPosts) {
            capped = true;
            break;
          }
          if (result.items.length === 0 || all.length >= total) break;
          page += 1;
        }

        if (cancelled) return;
        setState({ posts: all, total, capped, loading: false, error: null, loaded: true });
      } catch (err) {
        if (cancelled) return;
        setState((previous) => ({
          ...previous,
          loading: false,
          error: err instanceof ApiError ? err : new ApiError({ code: "network_error", message: "Failed to load posts." }),
        }));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [jobId, pageSize, maxPosts, attempt]);

  const reload = useCallback(() => setAttempt((value) => value + 1), []);

  return { ...state, reload };
}