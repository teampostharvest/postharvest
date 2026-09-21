import { describe, expect, it } from "vitest";
import {
  RobotsTxtPolicy,
  applyRules,
  parseRobots,
  type RobotsDecision,
} from "../src/fetch/robots.js";

describe("robots parsing (RFC 9309 subset)", () => {
  it("parses a * group and longest-match-wins", () => {
    const rules = parseRobots(`
User-agent: *
Disallow: /shell
Disallow: /about
Allow: /about/contact
`);
    expect(applyRules(rules, "/shell").allowed).toBe(false);
    expect(applyRules(rules, "/about").allowed).toBe(false);
    expect(applyRules(rules, "/about/contact").allowed).toBe(true);
    expect(applyRules(rules, "/home").allowed).toBe(true);
  });

  it("treats an empty Disallow as allow-all", () => {
    const rules = parseRobots(`
User-agent: *
Disallow:
`);
    expect(rules.length).toBe(1);
    expect(applyRules(rules, "/anything").allowed).toBe(true);
  });

  it("ignores comments and other records", () => {
    const rules = parseRobots(`
# robots.txt for facebook.com
User-agent: *
Disallow: /login
Sitemap: https://www.facebook.com/sitemap.xml
Crawl-delay: 5
`);
    expect(applyRules(rules, "/login").allowed).toBe(false);
    expect(applyRules(rules, "/timeline").allowed).toBe(true);
  });
});

describe("RobotsTxtPolicy", () => {
  async function canFetch(
    robotsTxt: string | null,
    targetUrl = "https://www.facebook.com/NASA",
  ): Promise<RobotsDecision> {
    const policy = new RobotsTxtPolicy({
      fetchText: async () => robotsTxt,
      now: () => 1_000_000,
    });
    return policy.canFetch(targetUrl);
  }

  it("allows when robots.txt is unavailable (allowlist-only policy)", async () => {
    const decision = await canFetch(null);
    expect(decision.allowed).toBe(true);
    expect(decision.reason).toBe("robots_unavailable");
  });

  it("denies when the path is disallowed", async () => {
    const decision = await canFetch(
      "User-agent: *\nDisallow: /NASA",
      "https://www.facebook.com/NASA",
    );
    expect(decision.allowed).toBe(false);
    expect(decision.reason).toContain("robots.txt disallows");
  });

  it("allows paths not covered by a rule", async () => {
    const decision = await canFetch(
      "User-agent: *\nDisallow: /login",
      "https://www.facebook.com/NASA",
    );
    expect(decision.allowed).toBe(true);
  });

  it("serves the cached robots.txt on the second query without refetching", async () => {
    const fetchText = async () =>
      "User-agent: *\nDisallow: /login";
    const policy = new RobotsTxtPolicy({
      fetchText,
      now: () => 1_000_000,
    });

    const first = await policy.canFetch(
      "https://www.facebook.com/NASA/posts/1",
    );
    const second = await policy.canFetch(
      "https://www.facebook.com/NASA/posts/2",
    );
    expect(first.allowed).toBe(true);
    expect(second.allowed).toBe(true);
  });
});