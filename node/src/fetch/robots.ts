/**
 * robots.txt policy for the fetcher.
 *
 * Mirrors backend/scraper/fetcher.py `_RobotsPolicy`:
 *  - Fetch `{origin}/robots.txt` once (per origin), best-effort.
 *  - If robots.txt cannot be fetched / errors, allow (allowlist-only policy).
 *  - If the resource path is disallowed, refuse the fetch.
 *
 * Parser is a minimal RFC 9309 subset: `User-agent:` groups, `Allow`/`Disallow`
 * prefix rules, longest-match-wins. `*` group applies to our honest UA
 * (matches what Python's urllib.robotparser would do with it).
 */

import { request } from "undici";
import { BROWSER_HEADERS } from "../config.js";
import { readBody } from "./util.js";

interface RobotsRule {
  allow: boolean;
  path: string;
}

interface GroupState {
  agent: string | null;
  rules: RobotsRule[];
}

export interface RobotsDecision {
  allowed: boolean;
  reason?: string;
}

export interface RobotsPolicy {
  canFetch(url: string): Promise<RobotsDecision>;
}

interface PolicyDeps {
  /** Fetch a URL's body text; return null on any failure. */
  fetchText?: (url: string) => Promise<string | null>;
  now?: () => number;
  cacheTtlMs?: number;
  robotsPath?: string; // test seam — defaults to /robots.txt
}

interface CachedRules {
  rules: RobotsRule[];
  fetchedAt: number;
}

const DEFAULT_CACHE_TTL_MS = 30 * 60 * 1000;

export class RobotsTxtPolicy implements RobotsPolicy {
  private readonly cache = new Map<string, CachedRules>();
  private readonly now: () => number;
  private readonly cacheTtlMs: number;

  constructor(private readonly deps: PolicyDeps = {}) {
    this.now = deps.now ?? Date.now;
    this.cacheTtlMs = deps.cacheTtlMs ?? DEFAULT_CACHE_TTL_MS;
  }

  async canFetch(url: string): Promise<RobotsDecision> {
    let target: URL;
    try {
      target = new URL(url);
    } catch {
      return { allowed: false, reason: "invalid_url" };
    }

    const origin = `${target.protocol}//${target.hostname}`;
    const path = target.pathname || "/";

    const rules = await this.rulesForOrigin(origin);
    if (rules === null) {
      // robots.txt unavailable — allowlist-only policy: never block scraping on
      // an unreadable robots.txt.
      return { allowed: true, reason: "robots_unavailable" };
    }

    const verdict = applyRules(rules, path);
    if (verdict.allowed) return { allowed: true };
    return { allowed: false, reason: `robots.txt disallows ${path}` };
  }

  private async rulesForOrigin(origin: string): Promise<RobotsRule[] | null> {
    const cached = this.cache.get(origin);
    if (cached && this.now() - cached.fetchedAt < this.cacheTtlMs) {
      return cached.rules;
    }

    const robotsPath = this.deps.robotsPath ?? "/robots.txt";
    const robotsUrl = `${origin}${robotsPath}`;
    const text = this.deps.fetchText
      ? await this.deps.fetchText(robotsUrl)
      : await fetchRobotsText(robotsUrl);

    const rules = text === null ? null : parseRobots(text);
    if (rules !== null) this.cache.set(origin, { rules, fetchedAt: this.now() });
    return rules;
  }
}

/** Fetch robots.txt with the honest UA and a short timeout; null on any failure. */
export async function fetchRobotsText(url: string): Promise<string | null> {
  try {
    const res = await request(url, {
      method: "GET",
      headers: BROWSER_HEADERS,
      headersTimeout: 10_000,
      bodyTimeout: 10_000,
    });
    if (res.statusCode !== 200) return null;
    const body = await readBody(
      res.body as unknown as AsyncIterable<Uint8Array>,
      512 * 1024,
    );
    return body.toString("utf8");
  } catch {
    return null;
  }
}

/**
 * Parse a robots.txt into the rules that apply to our user-agent.
 * Empty `Disallow:` is normalized to allow-all (RFC 9309 §2.2.2).
 */
export function parseRobots(raw: string): RobotsRule[] {
  const groups: GroupState[] = [];
  let current: GroupState | null = null;

  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (trimmed === "" || trimmed.startsWith("#")) continue;
    const sep = trimmed.indexOf(":");
    if (sep === -1) continue;
    const field = trimmed.slice(0, sep).trim().toLowerCase();
    const value = trimmed.slice(sep + 1).trim();

    if (field === "user-agent") {
      current = { agent: value.toLowerCase(), rules: [] };
      groups.push(current);
      continue;
    }
    if (!current) continue;

    if (field === "allow") {
      current.rules.push({ allow: true, path: value });
    } else if (field === "disallow") {
      if (value === "") {
        // Empty Disallow = allow everything (overrides any other rule).
        current.rules.push({ allow: true, path: "" });
      } else {
        current.rules.push({ allow: false, path: value });
      }
    }
  }

  // Apply rules from the `*` group; if there is no `*` group, treat nothing as
  // disallowed within a group so robots doesn't accidentally block. (Python's
  // robotparser with our UA matches `*` only.)
  const star = groups.find((g) => g.agent === "*");
  return star ? star.rules : groups[0]?.rules ?? [];
}

/**
 * Longest-prefix match: the rule whose path is the longest prefix of the
 * request path wins; ties resolve to Allow. No match → allowed.
 */
export function applyRules(rules: RobotsRule[], path: string): RobotsDecision {
  let best: RobotsRule | null = null;
  for (const rule of rules) {
    if (rule.path === "" || path.startsWith(rule.path)) {
      if (best === null || rule.path.length > best.path.length) best = rule;
    }
  }
  if (best === null) return { allowed: true };
  return best.allow ? { allowed: true } : { allowed: false };
}