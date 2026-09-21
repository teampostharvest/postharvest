"use client";

import { useEffect, useMemo, useState } from "react";
import { CircleCheck, CircleX, RotateCcw } from "lucide-react";
import { LaunchButton } from "@/components/common/LaunchButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, isFacebookUrl } from "@/lib/api";
import { readActiveAccount, readScrapeDefaults, writeActiveAccount } from "@/lib/settings";
import type { AccountSession, PostType, ScrapeRequest } from "@/lib/types";
import { cn, formatDateTime } from "@/lib/utils";

export interface ParsedUrl {
  raw: string;
  normalized: string | null;
  valid: boolean;
  reason: string | null;
}

export interface UrlInputCardProps {
  /** Lock all inputs while a job is running. */
  disabled?: boolean;
  submitting?: boolean;
  /** Seed the targets textarea (e.g. a URL piped in from the Home screen). */
  initialUrls?: string;
  onSubmit: (request: ScrapeRequest) => void;
  /** Called when the user edits the form, so a stale start error can clear. */
  onClearError?: () => void;
}

type TimePreset = "" | "7d" | "30d" | "90d" | "1y" | "custom";

const POST_TYPE_OPTIONS: ReadonlyArray<{ value: "" | PostType; label: string }> = [
  { value: "", label: "All post types" },
  { value: "text", label: "Text" },
  { value: "image", label: "Image" },
  { value: "video", label: "Video / Reel" },
  { value: "link", label: "Link" },
];

const TIME_FRAME_OPTIONS: ReadonlyArray<{ value: TimePreset; label: string }> = [
  { value: "", label: "All time" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
  { value: "1y", label: "Last year" },
  { value: "custom", label: "Custom" },
];

const SCROLL_ROUND_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "", label: "Auto (stop when no new posts)" },
  { value: "10", label: "10 rounds" },
  { value: "20", label: "20 rounds" },
  { value: "40", label: "40 rounds" },
  { value: "60", label: "60 rounds" },
  { value: "100", label: "100 rounds" },
  { value: "200", label: "200 rounds" },
  { value: "300", label: "300 rounds (max)" },
];

const FIELD_INPUT_CLASS = "h-9";

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs font-medium text-ink-muted">{children}</p>
  );
}

function FieldLabel({ htmlFor, children }: { htmlFor?: string; children: React.ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="mb-1.5 block text-sm font-medium text-ink">
      {children}
    </label>
  );
}

/** Rectangular segmented control — active is solid black, inactive white. */
function ToggleGroup<T extends string>({
  value,
  options,
  onChange,
  disabled,
  ariaLabel,
}: {
  value: T;
  options: ReadonlyArray<{ value: T; label: string }>;
  onChange: (value: T) => void;
  disabled?: boolean;
  ariaLabel: string;
}) {
  return (
    <div role="group" aria-label={ariaLabel} className="flex flex-wrap">
      {options.map((option, index) => {
        const active = value === option.value;
        return (
          <button
            key={option.value || "none"}
            type="button"
            onClick={() => onChange(option.value)}
            disabled={disabled}
            aria-pressed={active}
            className={cn(
              "-ml-px border px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-accent/30 first:ml-0",
              index > 0 && "-ml-px",
              active
                ? "border-ink bg-ink text-bg"
                : "border-border-strong bg-bg-elevated text-ink-muted hover:border-accent hover:text-ink"
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function UrlInputCard({
  disabled = false,
  submitting = false,
  initialUrls,
  onSubmit,
  onClearError,
}: UrlInputCardProps) {
  const [bulk, setBulk] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [maxPosts, setMaxPosts] = useState("");
  const [postType, setPostType] = useState<"" | PostType>("");
  const [useBrowser, setUseBrowser] = useState(false);
  const [account, setAccount] = useState("");
  const [scrolls, setScrolls] = useState("");
  const [timeFrame, setTimeFrame] = useState<TimePreset>("");
  const [accounts, setAccounts] = useState<AccountSession[]>([]);

  // Prefill persisted default options ("Settings → Default scrape options").
  useEffect(() => {
    const saved = readScrapeDefaults();
    if (saved.maxPosts) setMaxPosts(saved.maxPosts);
    if (saved.postType) setPostType(saved.postType);
    if (saved.scrolls) setScrolls(saved.scrolls);
    if (saved.useBrowser) setUseBrowser(true);
  }, []);

  // Load the saved sessions for the account dropdown (ops pool + my own).
  // When the sidebar selector (or a previous run) named an active session
  // that still exists, prefill it so the next run uses it.
  useEffect(() => {
    let cancelled = false;
    api
      .listAccounts()
      .then((res) => {
        if (!cancelled) {
          const all = [...(res.ops ?? []), ...(res.mine ?? [])];
          setAccounts(all);
          const active = readActiveAccount();
          if (active && all.some((entry) => `${entry.scope}:${entry.name}` === active)) {
            setAccount(active);
          }
        }
      })
      .catch(() => {
        // Backend unreachable — dropdown just stays on "anonymous".
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // A URL piped in from the Home screen lands in the targets box.
  useEffect(() => {
    if (initialUrls) setBulk(initialUrls);
  }, [initialUrls]);

  const setPresetDates = (preset: TimePreset) => {
    setTimeFrame(preset);
    if (preset === "" || preset === "custom") {
      setStartDate("");
      setEndDate("");
      return;
    }
    const days = preset === "7d" ? 7 : preset === "30d" ? 30 : preset === "90d" ? 90 : 365;
    const today = new Date();
    const start = new Date(today.getTime() - days * 86400000);
    setStartDate(start.toISOString().slice(0, 10));
    setEndDate(today.toISOString().slice(0, 10));
  };

  const parsedUrls = useMemo<ParsedUrl[]>(() => {
    const seen = new Set<string>();
    const output: ParsedUrl[] = [];
    for (const row of bulk.split(/\r?\n/)) {
      const raw = row.trim();
      if (!raw) continue;
      const key = raw.toLowerCase();
      if (seen.has(key)) continue; // dedupe identical lines
      seen.add(key);
      output.push({ raw, ...isFacebookUrl(raw) });
    }
    return output;
  }, [bulk]);

  const validCount = parsedUrls.filter((entry) => entry.valid).length;
  const invalidCount = parsedUrls.length - validCount;
  const duplicatedCount = useMemo(() => {
    const seen = new Set<string>();
    let dupes = 0;
    for (const row of bulk.split(/\r?\n/)) {
      const key = row.trim().toLowerCase();
      if (!key) continue;
      if (seen.has(key)) dupes += 1;
      seen.add(key);
    }
    return dupes;
  }, [bulk]);

  const dateRangeInvalid = startDate !== "" && endDate !== "" && startDate > endDate;
  const isCustomDate = timeFrame === "custom";
  const canSubmit = validCount > 0 && !dateRangeInvalid && !disabled && !submitting;
  const scrollOptions = useMemo(() => {
    const hasCustom = scrolls !== "" && !SCROLL_ROUND_OPTIONS.some((option) => option.value === scrolls);
    return hasCustom
      ? [...SCROLL_ROUND_OPTIONS, { value: scrolls, label: `${scrolls} rounds (custom)` }]
      : SCROLL_ROUND_OPTIONS;
  }, [scrolls]);

  const clearAll = () => {
    setBulk("");
    setStartDate("");
    setEndDate("");
    setMaxPosts("");
    setPostType("");
    setUseBrowser(false);
    setAccount("");
    setScrolls("");
    setTimeFrame("");
  };

  const handleSubmit = () => {
    if (!canSubmit) return;
    const urls = parsedUrls
      .filter((entry): entry is ParsedUrl & { normalized: string } => entry.valid && entry.normalized !== null)
      .map((entry) => entry.normalized);
    const parsedMax = Number.parseInt(maxPosts, 10);
    const parsedScrolls = Number.parseInt(scrolls, 10);
    const accountSpec = useBrowser && account.trim() !== "" ? account.trim() : null;
    // Remember the session as the panel's active account (anonymous runs
    // clear it — there is no backend default account).
    writeActiveAccount(accountSpec);
    onSubmit({
      urls,
      max_posts: Number.isFinite(parsedMax) && parsedMax > 0 ? parsedMax : null,
      start_date: startDate === "" ? null : startDate,
      end_date: endDate === "" ? null : endDate,
      post_type: postType === "" ? null : postType,
      use_browser: useBrowser,
      account: accountSpec,
      scrolls: useBrowser && Number.isFinite(parsedScrolls) && parsedScrolls > 0 ? parsedScrolls : null,
    });
  };

  return (
    <div className="mx-auto w-full max-w-5xl">
      {/* Targets */}
      <div>
        <Eyebrow>Targets</Eyebrow>
        <textarea
          id="bulk-urls"
          value={bulk}
          onChange={(event) => {
            setBulk(event.target.value);
            onClearError?.();
          }}
          placeholder={"https://www.facebook.com/examplepage"}
          disabled={disabled}
          rows={4}
          className="mt-2 w-full resize-y rounded-md border border-border-strong bg-bg-elevated p-4 font-mono text-sm text-ink placeholder:text-ink-faint focus:border-accent focus:outline-hidden"
        />
        <p className="mt-2 text-xs text-ink-muted">
          Paste one Facebook public page or profile URL per line
        </p>

        {/* Validation */}
        {parsedUrls.length > 0 ? (
          <div className="mt-4">
            <div className="flex flex-wrap items-center gap-1.5" role="status" aria-live="polite">
              {validCount > 0 ? (
                <span className="rounded-full bg-success/10 px-2 py-0.5 text-xs font-medium text-success">
                  {validCount} valid
                </span>
              ) : null}
              {invalidCount > 0 ? (
                <span className="rounded-full bg-danger/10 px-2 py-0.5 text-xs font-medium text-danger">
                  {invalidCount} invalid
                </span>
              ) : null}
              {duplicatedCount > 0 ? (
                <span className="rounded-full bg-bg-subtle px-2 py-0.5 text-xs font-medium text-ink-muted">
                  {duplicatedCount} duplicate line{duplicatedCount === 1 ? "" : "s"} ignored
                </span>
              ) : null}
            </div>
            <ul className="mt-1.5 max-h-40 space-y-1 overflow-y-auto">
              {parsedUrls.map((entry, index) => (
                <li key={`${entry.raw}-${index}`} className="flex items-start gap-2 font-mono text-xs">
                  {entry.valid ? (
                    <CircleCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" aria-hidden="true" />
                  ) : (
                    <CircleX className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" aria-hidden="true" />
                  )}
                  <span className={cn("min-w-0 break-all", entry.valid ? "text-ink" : "text-ink-faint line-through")}>
                    {entry.raw}
                    {entry.valid ? null : <span className="text-danger not-italic">, {entry.reason}</span>}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      {/* Configuration */}
      <div className="mt-8">
        <Eyebrow>Configuration</Eyebrow>

        <div className="mt-3">
          <p className="text-xs font-medium text-ink-muted">Filters</p>
          <FieldLabel htmlFor="time-frame">Time frame</FieldLabel>
          <ToggleGroup
            value={timeFrame}
            options={TIME_FRAME_OPTIONS}
            onChange={setPresetDates}
            disabled={disabled}
            ariaLabel="Time frame"
          />
          <p className="mt-1.5 text-xs text-ink-muted">
            {dateRangeInvalid
              ? "End date must be on or after start date."
              : isCustomDate
                ? startDate === "" && endDate === ""
                  ? "Pick a start and end date"
                  : `${startDate || "…"} → ${endDate || "…"}`
                : startDate === "" && endDate === ""
                  ? "No date filter (default)"
                  : `${startDate || "…"} → ${endDate || "…"}`}
          </p>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
          {isCustomDate ? (
            <>
              <div>
                <FieldLabel htmlFor="date-start">Start date</FieldLabel>
                <Input
                  id="date-start"
                  type="date"
                  value={startDate}
                  onChange={(event) => {
                    setStartDate(event.target.value);
                    setTimeFrame("custom");
                  }}
                  disabled={disabled}
                  className={FIELD_INPUT_CLASS}
                />
              </div>
              <div>
                <FieldLabel htmlFor="date-end">End date</FieldLabel>
                <Input
                  id="date-end"
                  type="date"
                  value={endDate}
                  onChange={(event) => {
                    setEndDate(event.target.value);
                    setTimeFrame("custom");
                  }}
                  disabled={disabled}
                  className={FIELD_INPUT_CLASS}
                />
              </div>
            </>
          ) : null}
          <div>
            <FieldLabel htmlFor="max-posts">Maximum posts</FieldLabel>
            <Input
              id="max-posts"
              type="number"
              min={1}
              step={1}
              value={maxPosts}
              onChange={(event) => setMaxPosts(event.target.value)}
              placeholder="No limit"
              disabled={disabled}
              className={FIELD_INPUT_CLASS}
            />
          </div>
          <div>
            <FieldLabel htmlFor="post-type">Post type</FieldLabel>
            <Select
              id="post-type"
              value={postType}
              onChange={(event) => setPostType(event.target.value as "" | PostType)}
              disabled={disabled}
              className={FIELD_INPUT_CLASS}
            >
              {POST_TYPE_OPTIONS.map((option) => (
                <option key={option.value || "all"} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {/* Browser mode */}
        <div className="mt-8">
          <label className="flex cursor-pointer items-start gap-3">
            <input
              type="checkbox"
              checked={useBrowser}
              onChange={(event) => setUseBrowser(event.target.checked)}
              disabled={disabled}
              className="mt-0.5 h-4 w-4 accent-ink"
            />
            <span className="space-y-0.5">
              <span className="block text-sm font-medium text-ink">Browser Mode (Playwright)</span>
              <span className="block text-xs leading-relaxed text-ink-muted">
                Scrapes the page's own GraphQL feed and scrolls to load more posts.
              </span>
            </span>
          </label>

          {useBrowser ? (
            <div className="mt-4 grid grid-cols-1 gap-4 pl-7 sm:grid-cols-2">
              <div>
                <FieldLabel htmlFor="account-name">Saved account (cookies)</FieldLabel>
                <Select
                  id="account-name"
                  value={account}
                  onChange={(event) => setAccount(event.target.value)}
                  disabled={disabled}
                  className={FIELD_INPUT_CLASS}
                >
                  <option value="">Anonymous (no saved session)</option>
                  {accounts.map((entry) => (
                    <option key={`${entry.scope}:${entry.name}`} value={`${entry.scope}:${entry.name}`}>
                      {entry.scope === "me" ? "me" : "ops"} / {entry.name}
                      {entry.saved_at ? ` — saved ${formatDateTime(entry.saved_at)}` : ""}
                      {entry.status === "VALID" ? "" : entry.status === "EXPIRED" ? " — needs attention" : " — unknown"}
                    </option>
                  ))}
                </Select>
                <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
                  Cookies unlock the full feed; anonymous sessions are capped by Facebook. Operators maintain the{" "}
                  <code className="font-mono">ops</code> pool; add your own from Saved Sessions.
                </p>
              </div>
              <div>
                <FieldLabel htmlFor="scroll-count">Scroll rounds</FieldLabel>
                <Select
                  id="scroll-count"
                  value={scrolls}
                  onChange={(event) => setScrolls(event.target.value)}
                  disabled={disabled}
                  className={FIELD_INPUT_CLASS}
                >
                  {scrollOptions.map((option) => (
                    <option key={option.value || "auto"} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {/* Action bar */}
      <div className="mt-8 flex flex-col-reverse items-stretch justify-between gap-4 border-t border-border pt-6 sm:flex-row sm:items-center">
        {disabled ? (
          <p className="text-xs text-ink-muted">
            Scraping in progress, inputs are locked until the job finishes.
          </p>
        ) : (
          <div className="flex flex-col gap-1">
            <Button
              type="button"
              variant="ghost"
              onClick={clearAll}
              disabled={submitting}
              className="justify-start px-0 text-sm font-medium text-ink-muted hover:bg-transparent hover:text-ink"
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" /> Clear
            </Button>
            {!useBrowser && accounts.length > 0 ? (
              <p className="text-xs text-ink-muted">
                Saved sessions apply to browser-mode runs only.
              </p>
            ) : null}
          </div>
        )}
        <LaunchButton
          onClick={handleSubmit}
          disabled={!canSubmit}
          loading={submitting}
          loadingLabel="Starting…"
        >
          Start Scraping
        </LaunchButton>
      </div>
    </div>
  );
}