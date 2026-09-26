import { describe, expect, it, vi } from "vitest";
import {
  captureFeed,
  type RootCandidate,
} from "../src/browser-mode/capture.js";
import type {
  CapturePage,
  CaptureResponse,
} from "../src/browser-mode/types.js";

const FB_URL = "https://www.facebook.com/NASA";

const ROOT_ONE: RootCandidate = {
  html: '<article id="p1"><p>First post body text padding</p></article>',
  text: "First post body text padding",
  aria: "",
};
const ROOT_TWO: RootCandidate = {
  html: '<article id="p2"><p>Second post body text padding</p></article>',
  text: "Second post body text padding",
  aria: "",
};
const ROOT_THREE: RootCandidate = {
  html: '<article id="p3"><p>Third post body text padding</p></article>',
  text: "Third post body text padding",
  aria: "",
};

const GQL_BODY_111 =
  '{"data":{"node":{"__typename":"Story","post_id":"111","creation_time":12345,"message":"first"}}}';
const GQL_BODY_222 =
  '{"data":{"node":{"__typename":"Story","post_id":"222","creation_time":12346,"message":"second"}}}';

function gqlResponse(url: string, body: string): CaptureResponse {
  return {
    url: vi.fn(() => url),
    text: vi.fn(async () => body),
  };
}

/** Observable state of the fake capture page during the loop. */
interface StageState {
  scrollTos: number;
  rootsByRound: Array<RootCandidate[]>;
  scriptsByRound: Array<string[]>;
  responseQueue: CaptureResponse[];
  loginCheck: { url: string; hasLoginForm: boolean; isLoginPage: boolean };
  tabClicked: boolean;
  finalDom: string;
}

type Staged = Partial<Omit<StageState, "scrollTos" | "tabClicked">> & {
  tabClicked?: boolean;
};

/**
 * Fake CapturePage. The inspectable in-page scripts are dispatched by source;
 * queued /api/graphql/ responses are emitted synchronously at each scroll so
 * round boundaries are deterministic (equivalent to them arriving during the
 * 1.5s scroll delay in a real browser).
 */
function fakePage(staged: Staged = {}): CapturePage & { state: StageState } {
  const state: StageState = {
    scrollTos: 0,
    rootsByRound: [],
    scriptsByRound: [],
    responseQueue: [],
    loginCheck: { url: FB_URL, hasLoginForm: false, isLoginPage: false },
    tabClicked: false,
    finalDom: staged.finalDom ?? '<div class="finaldom">end</div>',
    ...staged,
  };
  let responseCb: ((r: CaptureResponse) => void) | null = null;
  const page = {
    state,
    goto: vi.fn(async () => {}),
    waitForTimeout: vi.fn(async () => {}),
    url: vi.fn(() => FB_URL),
    content: vi.fn(async () => state.finalDom),
    evaluate: (async (fn: unknown) => {
      const src = String(fn);
      if (src.includes("scrollIntoView") || src.includes("window.scrollTo")) {
        const round = state.scrollTos;
        state.scrollTos += 1;
        const queued = state.responseQueue[round];
        if (queued && responseCb) responseCb(queued);
        return undefined;
      }
      if (src.includes('input[name="email"]')) return state.loginCheck;
      if (src.includes('[role="tab"]')) return state.tabClicked;
      if (src.includes("data-ad-preview")) {
        return state.rootsByRound[roundAt(state)] ?? [];
      }
      if (src.includes('querySelectorAll("script")')) {
        return state.scriptsByRound[roundAt(state)] ?? [];
      }
      return undefined;
    }) as CapturePage["evaluate"],
    on: vi.fn((event: string, cb: (r: CaptureResponse) => void) => {
      if (event === "response") responseCb = cb;
    }),
    querySelector: vi.fn(async () => null),
    click: vi.fn(async () => {}),
  };
  return page as unknown as CapturePage & { state: StageState };
}

/** Round index for the current extract call (extracts follow the scroll). */
function roundAt(state: StageState): number {
  return Math.max(0, state.scrollTos - 1);
}

describe("captureFeed", () => {
  it("assembles the snapshot in Python order with fresh posts and graphql", async () => {
    const page = fakePage({
      // Round 0: 2 retained roots (2 of 4 filtered below), 1 script blob,
      // 1 graphql body arriving during the scroll delay.
      rootsByRound: [
        [
          ROOT_ONE,
          { ...ROOT_TWO, aria: "Comment by Someone Else" }, // filtered: comment
          { ...ROOT_THREE, text: "tiny" }, // filtered: too short
          { ...ROOT_THREE, text: "Like Reply on this post body stuff" }, // filtered
          ROOT_THREE,
        ],
      ],
      scriptsByRound: [[`<script>${GQL_BODY_111}</script>`]],
      responseQueue: [
        gqlResponse("https://www.facebook.com/api/graphql/", GQL_BODY_111),
      ],
    });

    const result = await captureFeed(page, {
      url: FB_URL,
      scrollRounds: 3, // bound the patience window
    });

    expect(result.finalUrl).toBe(FB_URL);
    expect(result.stats).toEqual({
      loginWall: false,
      feedMissing: false,
      postsFound: 4, // 2 roots + 1 script + 1 graphql post_id
      stopReason: "EXHAUSTED",
    });
    // Exact assembly order: <html><body> DOM pool + script pool + gql blocks
    // + (no feed marker) + final DOM + </body></html>.
    expect(result.html).toBe(
      "<html><body>" +
        ROOT_ONE.html +
        ROOT_THREE.html +
        `<script>${GQL_BODY_111}</script>` +
        `<script type="application/json" data-fb-graphql-feed="1">${GQL_BODY_111}</script>` +
        '<div class="finaldom">end</div>' +
        "</body></html>",
    );
  });

  it("emits the feed-missing marker when no graphql body ever arrives", async () => {
    const page = fakePage({ rootsByRound: [[ROOT_ONE, ROOT_TWO]] });
    const result = await captureFeed(page, { url: FB_URL, scrollRounds: 2 });

    expect(result.stats.feedMissing).toBe(true);
    expect(result.html).toContain("<!-- fb-scrape-feed-missing -->");
    // Root posts still make it into the DOM pool.
    expect(result.html).toContain(ROOT_ONE.html);
    expect(result.html).toContain(ROOT_TWO.html);
  });

  it("dedupes graphql payloads by post_id and DOM roots by fingerprint", async () => {
    const page = fakePage({
      // Same roots offered every round: fingerprint dedupe -> pooled once.
      rootsByRound: [[ROOT_ONE, ROOT_TWO], [ROOT_ONE, ROOT_TWO], [ROOT_ONE, ROOT_TWO]],
      // 111 twice (second must be dropped), then 222 (kept).
      responseQueue: [
        gqlResponse("https://www.facebook.com/api/graphql/", GQL_BODY_111),
        gqlResponse("https://www.facebook.com/api/graphql/", GQL_BODY_111),
        gqlResponse("https://www.facebook.com/api/graphql/", GQL_BODY_222),
      ],
    });
    const result = await captureFeed(page, { url: FB_URL, scrollRounds: 4 });

    const gqlBlocks = (result.html.match(/data-fb-graphql-feed="1"/g) ?? []).length;
    expect(gqlBlocks).toBe(3);
    expect(result.html.match(/<article id="p1">/g)?.length).toBe(1);
    expect(result.html.match(/<article id="p2">/g)?.length).toBe(1);
    expect(result.stats.postsFound).toBe(4); // 2 roots + 2 unique post_ids
    expect(result.stats.feedMissing).toBe(false);
  });

  it("stops early on stale rounds when the feed is unreachable", async () => {
    const page = fakePage({}); // nothing ever arrives
    const result = await captureFeed(page, { url: FB_URL, scrollRounds: 20 });

    // No graphql -> stale_limit 10 (parity browser_scraper.py): the loop
    // aborts after 10 empty rounds instead of the full 20.
    expect(page.state.scrollTos).toBe(10);
    expect(result.html).toContain("<!-- fb-scrape-feed-missing -->");
  });

  it("short-circuits at max_posts", async () => {
    const page = fakePage({
      rootsByRound: [[ROOT_ONE, ROOT_TWO, ROOT_THREE]],
    });
    const result = await captureFeed(page, {
      url: FB_URL,
      scrollRounds: 10,
      maxPosts: 3,
    });

    expect(result.html).toContain(ROOT_ONE.html);
    expect(result.html).toContain(ROOT_TWO.html);
    expect(result.html).toContain(ROOT_THREE.html);
    expect(result.stats.postsFound).toBe(3);
    // Break happened at the end of round 0.
    expect(page.state.scrollTos).toBe(1);
    expect(result.stats.feedMissing).toBe(true);
  });

  it("reports the login wall in stats", async () => {
    const page = fakePage({
      loginCheck: {
        url: "https://www.facebook.com/login",
        hasLoginForm: true,
        isLoginPage: true,
      },
    });
    const result = await captureFeed(page, { url: FB_URL, scrollRounds: 1 });
    expect(result.stats.loginWall).toBe(true);
  });

  it("honours shouldAbort between rounds", async () => {
    const page = fakePage({});
    let checks = 0;
    const result = await captureFeed(page, {
      url: FB_URL,
      scrollRounds: 40,
      shouldAbort: () => ++checks > 2,
    });
    // Round 3 aborts -> exactly 2 scroll rounds completed.
    expect(page.state.scrollTos).toBe(2);
    expect(result.stats.feedMissing).toBe(true);
    expect(result.finalUrl).toBe(FB_URL);
  });
});