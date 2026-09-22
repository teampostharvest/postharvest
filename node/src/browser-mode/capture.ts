/**
 * Browser-mode capture (finalplanv2.md §4 browser-mode) — a faithful port of
 * `backend/scraper/browser_scraper.py::fetch_with_browser`.
 *
 * The algorithm drives a minimal `CapturePage` (Playwright's real Page
 * satisfies it; tests inject fakes) and assembles the exact synthetic HTML
 * snapshot the Python side produces, so `parse_browser_page` consumes the
 * Node capture byte-for-byte:
 *
 *   <html><body> DOM pool + script pool + data-fb-graphql-feed blocks
 *   + optional feed-missing marker + final DOM </body></html>
 *
 * The Comet feed loads further stories via POST /api/graphql/ responses
 * rather than new DOM, so those response bodies (raw, never interpreted)
 * are captured and embedded for the parser (backend parsing stays where it
 * is — Node returns bytes only).
 */

import { createHash } from "node:crypto";
import type {
  CapturePage,
  CaptureResponse,
} from "./types.js";

/** Parity: browser_scraper.py MAX_SCROLL_ROUNDS = 40. */
export const MAX_SCROLL_ROUNDS = 40;
/** Parity: browser_scraper.py SCROLL_DELAY = 1.5. */
export const SCROLL_DELAY_MS = 1500;

export interface BrowserCaptureOptions {
  url: string;
  /** 0/undefined == MAX_SCROLL_ROUNDS. */
  scrollRounds?: number;
  /** 0/undefined == no early stop. */
  maxPosts?: number;
  scrollDelayMs?: number;
  /** Parity with FastAPI's cancel_event (thread/worker cancellation). */
  shouldAbort?: () => boolean;
}

export interface BrowserCaptureStats {
  loginWall: boolean;
  feedMissing: boolean;
  postsFound: number;
}

export interface BrowserCaptureResult {
  html: string;
  finalUrl: string;
  stats: BrowserCaptureStats;
}

// ---------------------------------------------------------------------------
// In-page extraction scripts. These run inside Chromium (page.evaluate) and
// mirror find_post_roots / the script-blob collector from parser.py. The
// selectors are the drift surface — keep them in sync with:
//   backend/scraper/parser.py::find_post_roots (lines ~448-507)
// and the scroll-loop samples (fetch_with_browser).
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Minimal ambient DOM types for the in-page scripts below. These functions
// are serialized into Chromium (page.evaluate) and NEVER run in Node, so they
// only need enough typing to compile without pulling the DOM lib into this
// Node-targeted tsconfig (Node's own fetch/Response globals would collide).
// ---------------------------------------------------------------------------

type PageElement = {
  readonly outerHTML: string;
  readonly textContent: string | null;
  readonly innerText: string;
  readonly parentElement: PageElement | null;
  readonly tagName: string;
  getAttribute(name: string): string | null;
  click(): void;
};

declare const document: {
  querySelectorAll(selector: string): PageElement[];
  querySelector(selector: string): PageElement | null;
  readonly body: { readonly scrollHeight: number };
};

declare const window: {
  readonly location: { readonly href: string };
  scrollTo(x: number, y: number): void;
};

export type RootCandidate = { html: string; text: string; aria: string };

/** find_post_roots priority: article > [role=article] > data-ad-preview
 * (8-level walk-up) > [data-ft] > legacy class fallback. Nested candidates
 * are dropped so a post is never pooled twice. */
export const ROOT_EXTRACT_FN = (): RootCandidate[] => {
  const isAncestor = (ancestor: PageElement, tag: PageElement): boolean => {
    let current: PageElement | null = tag;
    for (let i = 0; i < 64; i++) {
      const parent: PageElement | null = current.parentElement;
      if (parent === null) return false;
      if (parent === ancestor) return true;
      current = parent;
    }
    return false;
  };

  let candidates: PageElement[] = [];
  const articles = document.querySelectorAll("article");
  if (articles.length > 0) {
    candidates = Array.from(articles);
  } else {
    const roleArticles = document.querySelectorAll('div[role="article"]');
    if (roleArticles.length > 0) {
      candidates = Array.from(roleArticles);
    } else {
      const adPreview = document.querySelectorAll('[data-ad-preview="message"]');
      if (adPreview.length > 0) {
        candidates = [];
        adPreview.forEach((tag) => {
          let parent: PageElement | null = tag.parentElement;
          let walked = false;
          for (let i = 0; i < 8; i++) {
            if (parent === null) {
              walked = true;
              break; // walked off the top: nothing appended (Python for-else)
            }
            if (
              parent.getAttribute("role") === "article" ||
              parent.tagName.toLowerCase() === "article"
            ) {
              walked = true;
              candidates.push(parent);
              break;
            }
            parent = parent.parentElement;
          }
          if (!walked) candidates.push(tag); // exhausted 8 levels: use tag
        });
      } else {
        const dataFt = document.querySelectorAll("[data-ft]");
        if (dataFt.length > 0) {
          candidates = Array.from(dataFt);
        } else {
          const classy = document.querySelectorAll(
            '[class*="story_body_container"], [class*="userContent"], [class*="fbUserPost"], [class*="story"]',
          );
          candidates = Array.from(classy).filter(
            (el) => (el.textContent || "").trim().length > 10,
          );
        }
      }
    }
  }

  const kept: PageElement[] = [];
  for (const tag of candidates) {
    if (kept.some((ancestor) => isAncestor(ancestor, tag))) continue;
    kept.push(tag);
  }
  // text normalization mirrors BeautifulSoup get_text(" ", strip=True) so the
  // fingerprint matches what the parser would see.
  return kept.map((el) => ({
    html: el.outerHTML,
    text: (el.innerText || "").replace(/\s+/g, " ").trim().slice(0, 4000),
    aria: el.getAttribute("aria-label") || "",
  }));
};

/** <script> tags whose text carries post data (Comet/Relay JSON), capped.
 * NOTE: this function runs INSIDE the browser, so no Node-side constants may
 * be referenced in its body (they serialize as bare identifiers and throw). */
export const SCRIPT_EXTRACT_FN = (): string[] => {
  return Array.from(document.querySelectorAll("script"))
    .map((s) => {
      const data = s.textContent || "";
      return { data, html: s.outerHTML };
    })
    .filter(
      (o) =>
        o.data.includes('"post_id"') && o.data.length < 200_000, // parity: browser_scraper.py:1232
    )
    .map((o) => o.html);
};

/** Dismiss cookie/login dialog buttons (parity fetch_with_browser ~:1110). */
export const POPUP_SELECTORS = [
  'button:has-text("Decline optional cookies")',
  'button:has-text("Accept all cookies")',
  'button:has-text("Not Now")',
  'div[role="dialog"] button[aria-label="Close"]',
  '[aria-label="Close"]',
] as const;

/** Login-wall check (parity ~:1126-1139). */
export const LOGIN_CHECK_FN = (): {
  url: string;
  hasLoginForm: boolean;
  isLoginPage: boolean;
} => {
  const url = window.location.href;
  const hasLoginForm = document.querySelector('input[name="email"]') !== null;
  const isLoginPage = url.includes("login") || url.includes("checkpoint");
  return { url, hasLoginForm, isLoginPage };
};

/** Click the "All" / "Posts" timeline tab so the feed renders on scroll. */
export const TAB_CLICK_FN = (): boolean => {
  const els = Array.from(document.querySelectorAll('[role="tab"]'));
  const target = els.find((el) =>
    /^\s*(All|Posts)\s*$/i.test((el.innerText || "").trim()),
  );
  if (target) {
    target.click();
    return true;
  }
  return false;
};

// ---------------------------------------------------------------------------
// Capture loop
// ---------------------------------------------------------------------------

const GRAPHQL_URL_SUFFIX = "/api/graphql/";
const POST_ID_RE = /"post_id"\s*:\s*"(\d+)"/g;

function fingerprint(text: string): string {
  return createHash("sha1").update(text, "utf8").digest("hex").slice(0, 24);
}

/**
 * Handle one browser response: keep raw `/api/graphql/` bodies that carry
 * post data, deduped by post_id (parity browser_scraper.py:1174-1189).
 */
async function collectFeedResponse(
  response: CaptureResponse,
  payloads: string[],
  seenIds: Set<string>,
): Promise<void> {
  try {
    if (!response.url().endsWith(GRAPHQL_URL_SUFFIX)) return;
    const body = await response.text();
    if (!body.includes('"post_id"') || !body.includes("creation_time")) return;

    const ids = new Set<string>();
    for (const match of body.matchAll(POST_ID_RE)) {
      const id = match[1];
      if (id !== undefined) ids.add(id);
    }
    if (ids.size === 0) return;
    let hasNew = false;
    for (const id of ids) {
      if (!seenIds.has(id)) {
        hasNew = true;
        break;
      }
    }
    if (!hasNew) return;
    for (const id of ids) seenIds.add(id);
    payloads.push(body);
  } catch {
    // A single bad response must never kill the capture (per-source isolation).
  }
}

async function dismissPopups(page: CapturePage): Promise<void> {
  for (const selector of POPUP_SELECTORS) {
    try {
      const el = await page.querySelector(selector);
      if (el) {
        await el.click();
        await page.waitForTimeout(500);
      }
    } catch {
      // selector absent -> try the next one
    }
  }
}

export async function captureFeed(
  page: CapturePage,
  opts: BrowserCaptureOptions,
): Promise<BrowserCaptureResult> {
  const scrollRounds = opts.scrollRounds ?? MAX_SCROLL_ROUNDS;
  const maxPosts = opts.maxPosts ?? 0;
  const scrollDelayMs = opts.scrollDelayMs ?? SCROLL_DELAY_MS;
  const shouldAbort = opts.shouldAbort ?? (() => false);

  // Navigate (parity: goto domcontentloaded, 30s) + settle.
  await page.goto(opts.url, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForTimeout(3000);

  await dismissPopups(page);

  let loginWall = false;
  try {
    const check = await page.evaluate(LOGIN_CHECK_FN);
    loginWall = check.isLoginPage || check.hasLoginForm;
  } catch {
    // evaluate failure is not a login wall
  }

  try {
    const clicked = await page.evaluate(TAB_CLICK_FN);
    if (clicked) await page.waitForTimeout(1500);
  } catch {
    // non-fatal: some pages have no timeline tab
  }

  // Comet feed loads the next batch of stories via POST /api/graphql/; capture
  // those payloads and embed them into the returned snapshot (parity:1171).
  const graphqlPayloads: string[] = [];
  const graphqlPostIds = new Set<string>();
  page.on("response", (response) => {
    void collectFeedResponse(response, graphqlPayloads, graphqlPostIds);
  });

  const domPool: string[] = [];
  const domSeen = new Set<string>();
  const scriptPool: string[] = [];
  const scriptSeen = new Set<string>();
  let staleRounds = 0;
  let postsFoundTotal = 0;

  for (let round = 0; round < scrollRounds; round++) {
    if (shouldAbort()) break;

    const gqlBefore = graphqlPostIds.size;
    await page.evaluate(() => {
      window.scrollTo(0, document.body.scrollHeight);
    });
    await page.waitForTimeout(scrollDelayMs);

    let freshNew = 0;

    // Snapshot the currently-mounted post containers (parity:1207-1227).
    let roots: RootCandidate[] = [];
    try {
      roots = await page.evaluate(ROOT_EXTRACT_FN);
    } catch {
      roots = [];
    }
    for (const root of roots) {
      if (root.aria.startsWith("Comment by")) continue;
      if (root.text.length < 20) continue;
      if (/\bLike\s*Reply\b/.test(root.text)) continue;
      const key = fingerprint(root.text);
      if (domSeen.has(key)) continue;
      domSeen.add(key);
      domPool.push(root.html);
      freshNew += 1;
    }

    // Capture embedded Comet/Relay JSON carrying post data (parity:1229-1238).
    let scripts: string[] = [];
    try {
      scripts = await page.evaluate(SCRIPT_EXTRACT_FN);
    } catch {
      scripts = [];
    }
    for (const html of scripts) {
      const key = fingerprint(html);
      if (scriptSeen.has(key)) continue;
      scriptSeen.add(key);
      scriptPool.push(html);
      freshNew += 1;
    }

    // Patience policy (parity:1240-1258): an unreachable Comet feed means the
    // only posts come from rendered DOM, so keep scrolling longer; when the
    // feed is alive but below target, Facebook sometimes resumes after a pause.
    const current = domPool.length + scriptPool.length + graphqlPostIds.size;
    const deficit = Math.max(0, (maxPosts || 9999) - current);
    const staleLimit =
      graphqlPostIds.size > 0
        ? Math.max(3, Math.min(Math.floor(deficit / 3), 12))
        : 6;

    if (freshNew === 0 && graphqlPostIds.size === gqlBefore) {
      staleRounds += 1;
      if (staleRounds >= staleLimit) break;
    } else {
      staleRounds = 0;
      postsFoundTotal = Math.max(postsFoundTotal, current);
    }

    if (maxPosts && domPool.length + graphqlPostIds.size >= maxPosts) break;
  }

  const gqlBlocks = graphqlPayloads
    .map(
      (payload) =>
        `<script type="application/json" data-fb-graphql-feed="1">${payload}</script>`,
    )
    .join("");

  // Marker: the Comet feed (graphql blocks carry the real timeline) never
  // loaded — a DOM-only snapshot means a partial/walled view (parity:1303).
  const feedMarker =
    graphqlPayloads.length === 0
      ? "<!-- fb-scrape-feed-missing -->"
      : "";

  let finalDom = "";
  try {
    finalDom = await page.content();
  } catch {
    finalDom = "";
  }

  // Byte-parity assembly (parity:1312-1320).
  const html =
    "<html><body>" +
    domPool.join("") +
    scriptPool.join("") +
    gqlBlocks +
    feedMarker +
    finalDom +
    "</body></html>";

  let finalUrl = opts.url;
  try {
    finalUrl = page.url();
  } catch {
    // keep opts.url
  }

  return {
    html,
    finalUrl,
    stats: {
      loginWall,
      feedMissing: graphqlPayloads.length === 0,
      postsFound: postsFoundTotal,
    },
  };
}