import { describe, expect, it } from "vitest";
import { NAV_ITEMS, getActiveLabel, isActiveHref, isDocsPath } from "@/lib/nav-config";

describe("NAV_ITEMS", () => {
  it("covers every primary section exactly once", () => {
    expect(NAV_ITEMS.map((item) => item.href)).toEqual([
      "/",
      "/investigation",
      "/pricing",
      "/history",
      "/accounts",
      "/settings",
    ]);
  });
});

describe("isActiveHref", () => {
  it("matches home exactly, never as a prefix", () => {
    expect(isActiveHref("/", "/")).toBe(true);
    expect(isActiveHref("/", "/history")).toBe(false);
  });

  it("matches section roots and their subroutes", () => {
    expect(isActiveHref("/investigation", "/investigation")).toBe(true);
    expect(isActiveHref("/investigation", "/investigation/abc")).toBe(true);
    expect(isActiveHref("/history", "/pricing")).toBe(false);
  });
});

describe("isDocsPath", () => {
  it("matches the docs root and slugs only", () => {
    expect(isDocsPath("/docs")).toBe(true);
    expect(isDocsPath("/docs/quickstart")).toBe(true);
    expect(isDocsPath("/history")).toBe(false);
  });
});

describe("getActiveLabel", () => {
  it("returns the matching section label", () => {
    expect(getActiveLabel("/")).toBe("Home");
    expect(getActiveLabel("/investigation")).toBe("Investigation");
    expect(getActiveLabel("/investigation/abc")).toBe("Investigation");
    expect(getActiveLabel("/pricing")).toBe("Pricing");
    expect(getActiveLabel("/history")).toBe("History");
    expect(getActiveLabel("/accounts")).toBe("Saved accounts");
    expect(getActiveLabel("/settings")).toBe("Settings");
  });

  it("labels docs routes as Docs", () => {
    expect(getActiveLabel("/docs")).toBe("Docs");
    expect(getActiveLabel("/docs/quickstart")).toBe("Docs");
  });

  it("falls back to the product name for unknown routes", () => {
    expect(getActiveLabel("/login")).toBe("PostHarvest");
  });
});
