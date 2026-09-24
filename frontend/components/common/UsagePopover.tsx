"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import type { AccountsResponse, UsageResponse } from "@/lib/types";
import {
  USAGE_RESET_LABEL,
  USAGE_TONE_TEXT,
  formatUpdateTime,
  planLabel,
  usagePillParts,
  usageValue,
} from "@/lib/usage";
import { cn } from "@/lib/utils";

/**
 * Top-bar usage pill + popover (plans/usagecomp.md).
 *
 * Pill  — `{Plan} · Pool x/y · Jobs x/y` (pool = personal sessions,
 *         jobs = concurrent jobs), the trigger. While open it gets a hairline
 *         accent border so it's clear which control opened the popover.
 * Popover — full usage detail with the bounded/unbounded value color rule:
 *         a capped resource is colored by remaining headroom (>=50% healthy,
 *         <50% warning, 0 exhausted); an uncapped resource is plain primary
 *         text. Only the current-count portion of a value is bolded.
 *
 * Data: `GET /api/usage` + `GET /api/accounts` (shared sessions = ops pool).
 * Closes on outside click / Escape / Close; the dialog traps Tab focus while
 * open and returns focus to the trigger on keyboard-initiated closes. Refetch
 * is guarded so rapid clicks can never stack duplicate fetches.
 */

interface UsageSnapshot {
  usage: UsageResponse;
  accounts: AccountsResponse;
  fetchedAt: number;
}

/** Re-fetch when the snapshot is older than this (popover opens fresh). */
const STALE_AFTER_MS = 30_000;
/** Gentle pill polling cadence — same as the dashboard's useUsage default. */
const POLL_MS = 30_000;

export function UsagePopover() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<UsageSnapshot | null>(null);
  const [loading, setLoading] = useState(false);

  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const fetchingRef = useRef(false);
  const snapshotRef = useRef<UsageSnapshot | null>(null);

  // Refs must not be written during render — sync the latest snapshot in an
  // effect so the open-time staleness check below can read it.
  useEffect(() => {
    snapshotRef.current = snapshot;
  }, [snapshot]);

  const fetchData = useCallback(async (): Promise<void> => {
    // Single-flight guard: never start a second fetch while one is in flight,
    // no matter how fast the refresh icon is clicked.
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    setLoading(true);
    try {
      const [usage, accounts] = await Promise.all([api.getUsage(), api.listAccounts()]);
      setSnapshot({ usage, accounts, fetchedAt: Date.now() });
    } catch {
      // Keep the last good snapshot; the refresh icon doubles as the retry
      // and the popover surfaces a fallback when nothing has ever loaded.
    } finally {
      fetchingRef.current = false;
      setLoading(false);
    }
  }, []);

  // First load + light polling while signed in. Paused for hidden tabs (a
  // background tab must not keep pinning a Postgres connection server-side).
  useEffect(() => {
    if (!user) return;
    void fetchData();
    const timer = window.setInterval(() => {
      if (typeof document === "undefined" || document.visibilityState !== "hidden") {
        void fetchData();
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [user, fetchData]);

  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    // Keyboard-initiated closes (Escape / Close) return focus to the pill;
    // pointer closes leave focus where the user aimed it.
    if (refocus) triggerRef.current?.focus();
  }, []);

  // Open-time refetch when the snapshot is stale, plus the popover's
  // dismissal + focus-trap behavior.
  useEffect(() => {
    if (!open) return;
    const snap = snapshotRef.current;
    if (!snap || Date.now() - snap.fetchedAt > STALE_AFTER_MS) void fetchData();

    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      close(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close(true);
        return;
      }
      if (event.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusables = Array.from(
        panel.querySelectorAll<HTMLElement>(
          'button, [href], input, [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    panelRef.current?.focus();
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, close, fetchData]);

  const rows = useMemo(() => {
    if (!snapshot) return null;
    const { usage, accounts } = snapshot;
    return [
      {
        id: "shared-sessions",
        label: "Shared sessions",
        value: usageValue(accounts.ops.length, null, "of"),
      },
      {
        id: "shared-jobs-today",
        label: "Shared jobs today",
        value: usageValue(usage.jobs_today, null, "slash"),
      },
      {
        id: "personal-sessions",
        label: "Personal sessions",
        value: usageValue(usage.personal_accounts.used, usage.personal_accounts.limit, "of"),
      },
      {
        id: "concurrent-jobs",
        label: "Concurrent jobs",
        value: usageValue(usage.jobs_running.used, usage.jobs_running.limit, "of"),
      },
      {
        id: "pool-availability",
        label: "Pool availability",
        value: usageValue(usage.jobs_running.used, usage.jobs_running.limit, "free"),
      },
    ];
  }, [snapshot]);

  if (!user) return null;

  const parts = usagePillParts(snapshot?.usage);
  const pillText = parts
    ? `${parts.plan} · Pool ${parts.pool.head}${parts.pool.tail} · Jobs ${parts.jobs.head}${parts.jobs.tail}`
    : "Usage";

  return (
    <div className="relative">
      <button
        type="button"
        ref={triggerRef}
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label="Plan and usage"
        title={pillText}
        className={cn(
          "hidden h-8 items-center gap-1.5 rounded-full border px-3 font-mono text-[11px] tabular-nums transition-colors md:flex",
          open
            ? "border-accent bg-bg-subtle text-ink"
            : "border-border bg-bg-subtle text-ink-muted hover:border-accent/60 hover:text-ink",
        )}
      >
        {parts ? (
          <>
            <span className="truncate font-sans text-xs font-semibold text-ink">{parts.plan}</span>
            <span aria-hidden="true" className="text-ink-faint">
              ·
            </span>
            <span className="inline-flex gap-0.5">
              Pool <span className="font-semibold text-ink">{parts.pool.head}</span>
              {parts.pool.tail}
            </span>
            <span aria-hidden="true" className="text-ink-faint">
              ·
            </span>
            <span className="inline-flex gap-0.5">
              Jobs <span className="font-semibold text-ink">{parts.jobs.head}</span>
              {parts.jobs.tail}
            </span>
          </>
        ) : (
          <span>Usage</span>
        )}
      </button>

      {open ? (
        <div
          ref={panelRef}
          role="dialog"
          aria-label={snapshot ? `Usage — ${planLabel(snapshot.usage.plan)}` : "Usage"}
          tabIndex={-1}
          className="absolute right-0 top-full z-50 mt-2 w-80 rounded-xl border border-usage-border bg-usage-bg shadow-lg outline-none"
        >
          <div className="flex items-center justify-between gap-2 border-b border-usage-border py-2.5 pl-4 pr-2">
            <h2 className="truncate text-sm font-semibold text-usage-title">
              {snapshot ? `Usage — ${planLabel(snapshot.usage.plan)}` : "Usage"}
            </h2>
            <button
              type="button"
              onClick={() => void fetchData()}
              disabled={loading}
              aria-label="Refresh usage"
              title="Refresh usage"
              className="flex h-7 w-7 items-center justify-center rounded-full text-usage-refresh transition-colors hover:bg-usage-close-hover hover:text-usage-refresh-hover focus:outline-hidden focus-visible:ring-2 focus-visible:ring-accent/25 disabled:cursor-default"
            >
              <RefreshCw
                strokeWidth={2}
                aria-hidden="true"
                className={cn("h-3.5 w-3.5", loading && "animate-spin text-usage-refresh-loading")}
              />
            </button>
          </div>

          {rows ? (
            <dl className="px-4 py-3">
              {rows.map((row) => (
                <div key={row.id} className="flex items-baseline justify-between gap-4 py-[5px]">
                  <dt className="shrink-0 text-[13px] leading-5 text-usage-label">{row.label}</dt>
                  <dd
                    data-tone={row.value.tone}
                    className={cn(
                      "truncate text-right text-[13px] leading-5 tabular-nums",
                      USAGE_TONE_TEXT[row.value.tone],
                    )}
                  >
                    <span className="font-semibold">{row.value.head}</span>
                    <span>{row.value.tail}</span>
                  </dd>
                </div>
              ))}
              <div className="flex items-baseline justify-between gap-4 border-t border-usage-border py-2">
                <dt className="shrink-0 text-[13px] leading-5 text-usage-label">
                  Resets daily at
                </dt>
                <dd className="truncate text-right text-[13px] leading-5 tabular-nums text-usage-label">
                  {USAGE_RESET_LABEL}
                </dd>
              </div>
            </dl>
          ) : loading ? (
            <div role="status" aria-label="Loading usage" className="px-4 py-3">
              {Array.from({ length: 6 }, (_, index) => (
                <div key={index} className="flex items-center justify-between gap-4 py-[6px]">
                  <div className="h-3 w-28 rounded-full bg-usage-label/15" />
                  <div className="h-3 w-14 rounded-full bg-usage-label/15" />
                </div>
              ))}
            </div>
          ) : (
            <p className="px-6 py-8 text-center text-xs text-usage-label">
              Couldn&apos;t load usage.
            </p>
          )}

          <div className="flex items-center justify-between gap-3 border-t border-usage-border py-2.5 pl-4 pr-3">
            <span className="text-[11px] leading-4 text-usage-footer">
              Updated {snapshot ? formatUpdateTime(snapshot.fetchedAt) : "—"}
            </span>
            <button
              type="button"
              onClick={() => close(true)}
              className="rounded px-2 py-1 text-xs font-medium text-usage-title transition-colors hover:bg-usage-close-hover focus:outline-hidden focus-visible:ring-2 focus-visible:ring-accent/25"
            >
              Close
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}