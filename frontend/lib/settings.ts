/**
 * Persisted user settings (localStorage). Currently: default scrape options
 * that prefill the "New scrape" form.
 */
import type { PostType } from "./types";

const DEFAULTS_KEY = "postharvest-defaults";

export interface ScrapeDefaults {
  maxPosts: string;
  postType: "" | PostType;
  scrolls: string;
  useBrowser: boolean;
}

export const EMPTY_DEFAULTS: ScrapeDefaults = {
  maxPosts: "",
  postType: "",
  scrolls: "",
  useBrowser: false,
};

export function readScrapeDefaults(): ScrapeDefaults {
  const loaded: ScrapeDefaults = { ...EMPTY_DEFAULTS };
  try {
    const raw = window.localStorage.getItem(DEFAULTS_KEY);
    if (!raw) return loaded;
    const parsed = JSON.parse(raw) as Partial<ScrapeDefaults>;
    if (typeof parsed.maxPosts === "string") loaded.maxPosts = parsed.maxPosts;
    if (parsed.postType === "text" || parsed.postType === "image" || parsed.postType === "video" || parsed.postType === "link") {
      loaded.postType = parsed.postType;
    }
    if (typeof parsed.scrolls === "string") loaded.scrolls = parsed.scrolls;
    if (typeof parsed.useBrowser === "boolean") loaded.useBrowser = parsed.useBrowser;
  } catch {
    // Unreadable JSON — fall back to the empty defaults.
  }
  return loaded;
}

export function writeScrapeDefaults(defaults: ScrapeDefaults): void {
  try {
    window.localStorage.setItem(DEFAULTS_KEY, JSON.stringify(defaults));
  } catch {
    // localStorage unavailable (private mode) — allow it to no-op.
  }
}

const LAST_ACCOUNT_KEY = "postharvest-last-account";
const ACTIVE_ACCOUNT_KEY = "postharvest-active-account";

/**
 * The `scope:name` session prefilled for the next run (or null for
 * anonymous). Set from the sidebar account selector; the run form also
 * writes it on submit. The backend has no default-account concept, so this
 * client-side value is what "active account" means everywhere in the UI.
 * Reads the legacy `postharvest-last-account` key once as a migration.
 */
export function readActiveAccount(): string | null {
  try {
    const raw =
      window.localStorage.getItem(ACTIVE_ACCOUNT_KEY) ??
      window.localStorage.getItem(LAST_ACCOUNT_KEY);
    if (!raw) return null;
    const trimmed = raw.trim();
    return trimmed === "" ? null : trimmed;
  } catch {
    return null;
  }
}

export function writeActiveAccount(spec: string | null): void {
  try {
    if (spec && spec.trim() !== "") {
      window.localStorage.setItem(ACTIVE_ACCOUNT_KEY, spec.trim());
    } else {
      window.localStorage.removeItem(ACTIVE_ACCOUNT_KEY);
    }
    window.localStorage.removeItem(LAST_ACCOUNT_KEY);
  } catch {
    // localStorage unavailable (private mode) — allow it to no-op.
  }
}

/** @deprecated Use readActiveAccount (kept for the migration fallback). */
export function readLastAccount(): string | null {
  return readActiveAccount();
}

/** @deprecated Use writeActiveAccount. */
export function writeLastAccount(spec: string | null): void {
  writeActiveAccount(spec);
}