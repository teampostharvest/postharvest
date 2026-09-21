"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { MotionIcon } from "motion-icons-react";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth-context";
import { useAccounts, useActiveJobs } from "@/lib/hooks";
import { readActiveAccount, writeActiveAccount } from "@/lib/settings";
import type { AccountSession, JobSummary } from "@/lib/types";
import { cn, formatCompact, parseDate } from "@/lib/utils";

/**
 * Live operations panel on the light rail family (never the black block).
 * No primary nav links live here. Top to bottom: active jobs and the
 * account selector (combobox pill + category tree + searchable modal — the
 * list never renders hundreds of rows flat).
 *
 * "Active account" is client-side truth: the `scope:name` session prefilled
 * for the next run (see lib/settings). Selecting one writes it; the run
 * form prefills it when it still exists.
 *
 * Collapse means unmounted: when the user collapses the panel the layout
 * removes this rail entirely (no icon column left behind) and shows only a
 * floating expand toggle. There is no icon-rail mode.
 */

function specOf(account: AccountSession): string {
  return `${account.scope}:${account.name}`;
}

function accountHealth(account: AccountSession): "healthy" | "attention" {
  // get_cookie_status only reports VALID/EXPIRED (no quarantine state exists
  // yet) — red is reserved for a future quarantined status.
  return account.status === "VALID" ? "healthy" : "attention";
}

function HealthBadge({ account }: { account: AccountSession }) {
  const healthy = accountHealth(account) === "healthy";
  return (
    <span
      className={cn(
        "shrink-0 rounded-full px-2 py-0.5 font-sans text-xs font-medium",
        healthy ? "bg-badge-ok text-badge-ok-fg" : "bg-badge-warn text-badge-warn-fg",
      )}
    >
      {healthy ? "healthy" : "attention"}
    </span>
  );
}

function PanelHeading({ children }: { children: React.ReactNode }) {
  return <p className="text-xs font-medium text-ink-muted">{children}</p>;
}

/** Inline failure state for polled sections: never a permanent loader. */
function PollError({ label, onRetry }: { label: string; onRetry: () => void }) {
  return (
    <p className="text-xs text-ink-muted">
      Couldn&apos;t load {label}{" "}
      <button
        type="button"
        onClick={onRetry}
        className="text-ink underline underline-offset-2 transition-colors hover:text-accent"
      >
        Retry
      </button>
    </p>
  );
}

function formatElapsed(createdAt: string | null | undefined, now: number): string {
  const parsed = parseDate(createdAt ?? null);
  if (!parsed) return "—";
  const seconds = Math.max(0, Math.floor((now - parsed.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

function jobProgress(job: JobSummary): number | null {
  if (job.pages_total > 0) {
    return Math.min(1, Math.max(0, job.pages_completed / job.pages_total));
  }
  return null;
}

function ActiveJobRow({ job, now }: { job: JobSummary; now: number }) {
  const target = job.urls[0] ?? job.job_id.slice(0, 8);
  const progress = jobProgress(job);
  const running = job.status === "running";
  return (
    <Link
      href={`/investigation?job=${encodeURIComponent(job.job_id)}`}
      title={`${target} — open live view`}
      aria-label={`${target} — open live view`}
      className="block rounded-lg border border-rail-border bg-card px-2.5 py-2 shadow-sm transition-colors hover:border-shell-accent"
    >
      <div className="flex items-center gap-2">
        <MotionIcon
          name="Radio"
          size={13}
          aria-hidden="true"
          animation={running ? "pulse" : "none"}
          trigger={running ? "always" : "hover"}
          className={cn("shrink-0", running ? "text-shell-accent" : "text-ink-faint")}
        />
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-rail-ink">{target}</span>
        <span className="shrink-0 font-mono text-xs tabular-nums text-ink-muted">
          {formatElapsed(job.created_at, now)}
        </span>
      </div>
      <div
        className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-rail-border"
        role="progressbar"
        aria-label={`Progress for ${target}`}
        aria-valuenow={progress == null ? undefined : Math.round(progress * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={cn(
            "h-full rounded-full bg-shell-accent transition-[width]",
            progress == null && "animate-pulse-dot",
          )}
          style={progress == null ? { width: "35%" } : { width: `${progress * 100}%` }}
        />
      </div>
      <p className="mt-1 flex items-baseline justify-between gap-2 font-mono text-xs tabular-nums text-ink-muted">
        <span>{formatCompact(job.posts_processed)} posts</span>
        {job.status === "queued" ? (
          <span className="shrink-0 rounded-full bg-rail-border/60 px-1.5 py-px font-sans text-xs text-ink-muted">
            queued
          </span>
        ) : progress != null ? (
          <span className="shrink-0">{Math.round(progress * 100)}%</span>
        ) : null}
      </p>
    </Link>
  );
}

function ActiveJobsSection({ now }: { now: number }) {
  const { user } = useAuth();
  const { jobs, error, reload } = useActiveJobs({ enabled: !!user });

  return (
    <section aria-label="Active jobs" className="flex flex-col gap-2">
      <PanelHeading>Active jobs</PanelHeading>
      {error && jobs.length === 0 ? (
        <PollError label="active jobs" onRetry={reload} />
      ) : jobs.length === 0 ? (
        <p className="text-xs text-ink-muted">No jobs running</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {jobs.map((job) => (
            <li key={job.job_id}>
              <ActiveJobRow job={job} now={now} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Account selector: pill combobox + category tree + searchable modal.
// ---------------------------------------------------------------------------

interface AccountLists {
  mine: AccountSession[];
  ops: AccountSession[];
}

function resolveActive(all: AccountSession[]): AccountSession | null {
  if (all.length === 0) return null;
  const stored = readActiveAccount();
  const match = stored ? all.find((entry) => specOf(entry) === stored) : undefined;
  if (match) return match;
  return all.find((entry) => accountHealth(entry) === "healthy") ?? all[0];
}

function matchesQuery(account: AccountSession, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return account.name.toLowerCase().includes(q) || account.scope.includes(q);
}

function AccountOption({
  account,
  selected,
  onSelect,
}: {
  account: AccountSession;
  selected: boolean;
  onSelect: (account: AccountSession) => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      onClick={() => onSelect(account)}
      className={cn(
        "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors",
        selected ? "bg-bg-subtle" : "hover:bg-bg-subtle",
      )}
    >
      <span className="min-w-0 flex-1 truncate font-mono text-[11px] font-medium text-rail-ink">
        {account.name}
      </span>
      <HealthBadge account={account} />
    </button>
  );
}

function AccountsModal({
  open,
  onClose,
  lists,
  active,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  lists: AccountLists;
  active: AccountSession | null;
  onSelect: (account: AccountSession) => void;
}) {
  const [query, setQuery] = useState("");
  useEffect(() => {
    if (open) setQuery("");
  }, [open ]);

  const filtered = useMemo(() => {
    const mine = lists.mine.filter((entry) => matchesQuery(entry, query));
    const ops = lists.ops.filter((entry) => matchesQuery(entry, query));
    return { mine, ops };
  }, [lists, query]);

  const total = lists.mine.length + lists.ops.length;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Select account"
      description={
        total === 0
          ? "No saved sessions yet."
          : `Search across ${total} saved session${total === 1 ? "" : "s"}.`
      }
      size="md"
    >
      <div className="flex flex-col gap-4">
        <input
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search name or scope…"
          aria-label="Search accounts"
          autoComplete="off"
          spellCheck={false}
          autoFocus
          className="h-9 w-full rounded-sm border border-border-strong bg-bg-elevated px-3 text-sm text-ink placeholder:text-ink-faint focus:outline-hidden focus-visible:border-accent focus-visible:ring-[3px] focus-visible:ring-accent/15"
        />
        {filtered.mine.length === 0 && filtered.ops.length === 0 ? (
          <p className="py-4 text-center text-xs text-ink-muted">
            {query ? "No accounts match your search" : "No saved accounts yet"}
          </p>
        ) : (
          <>
            {filtered.mine.length > 0 ? (
              <div className="flex flex-col gap-1">
                <p className="text-xs font-medium text-ink-muted">
                  Personal ({filtered.mine.length})
                </p>
                <div role="listbox" aria-label="Personal accounts" className="flex flex-col gap-0.5">
                  {filtered.mine.map((entry) => (
                    <AccountOption
                      key={`me:${entry.name}`}
                      account={entry}
                      selected={active != null && specOf(active) === specOf(entry)}
                      onSelect={onSelect}
                    />
                  ))}
                </div>
              </div>
            ) : null}
            {filtered.ops.length > 0 ? (
              <div className="flex flex-col gap-1">
                <p className="text-xs font-medium text-ink-muted">
                  Ops pool ({filtered.ops.length})
                </p>
                <div role="listbox" aria-label="Ops pool accounts" className="flex flex-col gap-0.5">
                  {filtered.ops.map((entry) => (
                    <AccountOption
                      key={`ops:${entry.name}`}
                      account={entry}
                      selected={active != null && specOf(active) === specOf(entry)}
                      onSelect={onSelect}
                    />
                  ))}
                </div>
              </div>
            ) : null}
          </>
        )}
      </div>
    </Dialog>
  );
}

function TreeRow({
  indent = true,
  onClick,
  label,
  detail,
  action = false,
}: {
  indent?: boolean;
  onClick: () => void;
  label: React.ReactNode;
  detail?: React.ReactNode;
  action?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2 rounded-md py-1.5 pr-2 text-left text-sm transition-colors",
        indent && "pl-5",
        action ? "text-ink-muted hover:text-ink" : "text-ink-muted hover:bg-bg-subtle hover:text-ink",
      )}
    >
      <span className="min-w-0 flex-1 truncate text-left">{label}</span>
      {detail}
    </button>
  );
}

function AccountsSection() {
  const { user } = useAuth();
  const { accounts, error, reload } = useAccounts({ enabled: !!user });
  const [pickerOpen, setPickerOpen] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [scopeFilter, setScopeFilter] = useState<"all" | "me" | "ops">("all");
  const [query, setQuery] = useState("");
  // The popover lives in a portal (outside the sidebar's overflow-y-auto
  // scroller, which would otherwise clip it) and is anchored to the pill.
  const pillRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [anchor, setAnchor] = useState<{ top?: number; bottom?: number; left: number; width: number } | null>(null);

  const lists = useMemo<AccountLists>(
    () => ({ mine: accounts?.mine ?? [], ops: accounts?.ops ?? [] }),
    [accounts],
  );
  const all = useMemo(() => [...lists.mine, ...lists.ops], [lists]);
  // Re-read on every render so a selection made in the modal (or a run
  // form elsewhere) reflects immediately without waiting for a poll tick.
  const active = resolveActive(all);

  useEffect(() => {
    if (!pickerOpen) return;
    const updateAnchor = () => {
      const rect = pillRef.current?.getBoundingClientRect();
      if (!rect) return;
      // Flip upward when there isn't room below.
      if (rect.bottom + 320 > window.innerHeight && rect.top > window.innerHeight - rect.bottom) {
        setAnchor({ bottom: window.innerHeight - rect.top + 6, left: rect.left, width: rect.width });
      } else {
        setAnchor({ top: rect.bottom + 6, left: rect.left, width: rect.width });
      }
    };
    updateAnchor();
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (pillRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setPickerOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPickerOpen(false);
    };
    // Capture scroll anywhere (the sidebar scroller moves the pill).
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("scroll", updateAnchor, true);
    window.addEventListener("resize", updateAnchor);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("scroll", updateAnchor, true);
      window.removeEventListener("resize", updateAnchor);
    };
  }, [pickerOpen]);

  const openPicker = (scope: "all" | "me" | "ops") => {
    setScopeFilter(scope);
    setQuery("");
    setPickerOpen(true);
  };

  const select = (account: AccountSession) => {
    writeActiveAccount(specOf(account));
    setPickerOpen(false);
    setModalOpen(false);
  };

  const visible =
    scopeFilter === "all"
      ? all
      : all.filter((entry) => entry.scope === (scopeFilter === "me" ? "me" : "ops"));
  const searched = visible.filter((entry) => matchesQuery(entry, query));
  const shown = searched.slice(0, 6);
  const hiddenCount = searched.length - shown.length;

  return (
    <section aria-label="Saved accounts" className="flex flex-col gap-2">
      <PanelHeading>Saved accounts</PanelHeading>

      {error && accounts == null ? (
        <PollError label="accounts" onRetry={reload} />
      ) : accounts == null ? (
        <div className="flex flex-col gap-1.5" role="status" aria-label="Loading accounts">
          <Skeleton className="h-9 w-full rounded-full" />
          <Skeleton className="h-9 w-full" />
          <div className="flex flex-col gap-1 pl-5 pt-1">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        </div>
      ) : (
        <>
          <div ref={pillRef}>
            <button
              type="button"
              onClick={() => openPicker("all")}
              aria-expanded={pickerOpen}
              aria-haspopup="listbox"
              aria-label={active ? `Active account for browser runs: ${active.name}` : "Select account"}
              title={active ? `Active account for browser runs: ${active.name}` : "Select account"}
              className={cn(
                "flex w-full items-center gap-2 rounded-full border bg-card px-3 py-2 text-left shadow-sm transition-all duration-200",
                pickerOpen
                  ? "border-shell-accent shadow"
                  : "border-rail-border hover:shadow",
              )}
            >
              {active ? (
                <>
                  <span className="min-w-0 flex-1 truncate font-mono text-[12px] font-bold text-rail-ink">
                    {active.name}
                  </span>
                  <HealthBadge account={active} />
                </>
              ) : (
                <span className="min-w-0 flex-1 truncate font-sans text-sm text-ink-muted">
                  Select account
                </span>
              )}
              <MotionIcon name="ChevronDown" size={14} aria-hidden="true" animation="nudge" trigger="hover" />
            </button>
            {typeof document !== "undefined"
              ? createPortal(
                  <AnimatePresence initial={false}>
                    {pickerOpen && anchor ? (
                      <motion.div
                        key="account-picker"
                        ref={panelRef}
                        initial={{ opacity: 0, y: (anchor.top != null ? -6 : 6), scale: 0.98 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, y: (anchor.top != null ? -4 : 4), scale: 0.98 }}
                        transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
                        style={{
                          position: "fixed",
                          top: anchor.top,
                          bottom: anchor.bottom,
                          left: anchor.left,
                          width: anchor.width,
                          transformOrigin: anchor.top != null ? "top center" : "bottom center",
                        }}
                        className="z-50 rounded-xl border border-rail-border bg-card p-2 shadow-lg"
                      >  <div className="flex gap-1 pb-1.5" role="group" aria-label="Scope filter">
                  {(["all", "me", "ops"] as const).map((scope) => (
                    <button
                      key={scope}
                      type="button"
                      onClick={() => setScopeFilter(scope)}
                      aria-pressed={scopeFilter === scope}
                      className={cn(
                        "rounded-full px-2.5 py-1 text-xs font-medium transition-colors",
                        scopeFilter === scope
                          ? "bg-ink text-bg"
                          : "text-ink-muted hover:text-ink",
                      )}
                    >
                      {scope === "all" ? "All" : scope === "me" ? "Personal" : "Ops pool"}
                    </button>
                  ))}
                </div>
                <input
                  type="text"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search…"
                  aria-label="Search accounts"
                  autoComplete="off"
                  spellCheck={false}
                  autoFocus
                  className="mb-1 h-8 w-full rounded-sm border border-border-strong bg-transparent px-2.5 text-sm text-ink placeholder:text-ink-faint focus:outline-hidden focus-visible:border-accent focus-visible:ring-[3px] focus-visible:ring-accent/15"
                />
                <div role="listbox" aria-label="Accounts" className="flex max-h-56 flex-col gap-0.5 overflow-y-auto">
                  {shown.length === 0 ? (
                    <p className="px-2.5 py-3 text-center text-xs text-ink-muted">
                      {query || scopeFilter !== "all" ? "No matches" : "No saved accounts"}
                    </p>
                  ) : (
                    shown.map((entry) => (
                      <AccountOption
                        key={specOf(entry)}
                        account={entry}
                        selected={active != null && specOf(active) === specOf(entry)}
                        onSelect={select}
                      />
                    ))
                  )}
                </div>
                {hiddenCount > 0 || searched.length > 0 ? (
                  <button
                    type="button"
                    onClick={() => {
                      setPickerOpen(false);
                      setModalOpen(true);
                    }}
                    className="mt-1 w-full rounded-md px-2.5 py-1.5 text-left text-sm text-ink-muted transition-colors hover:bg-bg-subtle hover:text-ink"
                  >
                    View all accounts…
                  </button>
                ) : null}
                      </motion.div>
                    ) : null}
                  </AnimatePresence>,
                  document.body,
                )
              : null}
          </div>

          <div className="flex flex-col gap-0.5" role="group" aria-label="Account shortcuts">
            <TreeRow
              label="Active account"
              detail={
                <span className="max-w-[7rem] truncate font-mono text-[11px] text-ink-muted">
                  {active ? active.name : "—"}
                </span>
              }
              onClick={() => openPicker("all")}
            />
            <TreeRow
              label="Personal"
              detail={
                <span className="rounded-full bg-bg-subtle px-1.5 py-0.5 font-mono text-[11px] tabular-nums text-ink-muted">
                  {lists.mine.length}
                </span>
              }
              onClick={() => openPicker("me")}
            />
            <TreeRow
              label="Ops pool"
              detail={
                <span className="rounded-full bg-bg-subtle px-1.5 py-0.5 font-mono text-[11px] tabular-nums text-ink-muted">
                  {lists.ops.length}
                </span>
              }
              onClick={() => openPicker("ops")}
            />
            <TreeRow
              label="View all accounts…"
              action
              onClick={() => setModalOpen(true)}
            />
          </div>
        </>
      )}

      <Link
        href="/accounts"
        className="flex items-center gap-1.5 text-sm text-ink-muted transition-colors hover:text-ink"
      >
        <MotionIcon name="Plus" size={14} aria-hidden="true" animation="pop" trigger="hover" />
        Add account
      </Link>

      <AccountsModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        lists={lists}
        active={active}
        onSelect={select}
      />
    </section>
  );
}

export function OpsSidebar() {
  // Re-render every 30s so elapsed-time labels stay fresh between polls.
  const [now, setNow] = useState(() => Date.now());
  const { user } = useAuth();

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);

  // Signed-out visitors get a static note — never a stuck "Loading…" state
  // (the hooks stay disabled without a session).
  if (!user) {
    return (
      <aside aria-label="Operations panel" className="flex h-full w-[264px] flex-col gap-3 bg-rail-bg px-4 py-4 text-rail-ink">
        <p className="text-xs leading-relaxed text-ink-muted">
          Sign in to see live jobs, accounts and usage.
        </p>
      </aside>
    );
  }

  return (
    <aside
      aria-label="Operations panel"
      className="flex h-full w-[264px] flex-col border-r border-rail-border bg-rail-bg text-rail-ink"
    >
      <div className="flex flex-1 flex-col gap-6 overflow-y-auto px-4 py-3">
        <ActiveJobsSection now={now} />
        <AccountsSection />
      </div>
    </aside>
  );
}
