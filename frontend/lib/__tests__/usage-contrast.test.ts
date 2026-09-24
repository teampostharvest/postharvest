import { describe, expect, it } from "vitest";

/**
 * WCAG AA contrast guard for the usage popover (plans/usagecomp.md
 * acceptance: "all text/background pairs in both themes pass WCAG AA
 * contrast, 4.5:1 for the 13-14px row text").
 *
 * The hex values below must stay in sync with the `--usage-*` tokens in
 * `app/globals.css` (light `:root/.light` and `.dark` scopes). The plan's
 * original table shipped values that fail AA on the light surface
 * (#78746A label, #3F8452 healthy, #B8862E warning, #8F8B80 footer) and in
 * dark (#6B675E footer); the tokens use hue-preserving variants that pass.
 */

function relativeLuminance(hex: string): number {
  const h = hex.replace("#", "");
  const channels = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const linear = channels.map((c) =>
    c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4),
  );
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(fg: string, bg: string): number {
  const l1 = relativeLuminance(fg);
  const l2 = relativeLuminance(bg);
  const [hi, lo] = l1 >= l2 ? [l1, l2] : [l2, l1];
  return (hi + 0.05) / (lo + 0.05);
}

const DARK_BG = "#151412";
const LIGHT_BG = "#FBFAF6";

function expectAA(fg: string, bg: string, label: string): void {
  const ratio = contrastRatio(fg, bg);
  expect(ratio, `${label} (${fg} on ${bg}) must be >= 4.5:1, got ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(4.5);
}

function expectIcon(fg: string, bg: string, label: string): void {
  const ratio = contrastRatio(fg, bg);
  expect(ratio, `${label} (${fg} on ${bg}) must be >= 3:1, got ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(3);
}

describe("usage popover — WCAG AA contrast (dark)", () => {
  it("text pairs clear 4.5:1", () => {
    expectAA("#F3F0E8", DARK_BG, "dark title/unbounded");
    expectAA("#8A8578", DARK_BG, "dark label/exhausted");
    expectAA("#7FB88A", DARK_BG, "dark value healthy");
    expectAA("#E8B94D", DARK_BG, "dark value warning");
    expectAA("#857F73", DARK_BG, "dark footer timestamp");
  });

  it("close-button text clears AA against its hover surface", () => {
    expectAA("#F3F0E8", "#1E1D1A", "dark close hover");
  });
});

describe("usage popover — WCAG AA contrast (light)", () => {
  it("text pairs clear 4.5:1", () => {
    expectAA("#1A1917", LIGHT_BG, "light title/unbounded");
    expectAA("#716C60", LIGHT_BG, "light label/exhausted");
    expectAA("#3A794B", LIGHT_BG, "light value healthy");
    expectAA("#8A6523", LIGHT_BG, "light value warning");
    expectAA("#6B675E", LIGHT_BG, "light footer timestamp");
  });

  it("close-button text clears AA against its hover surface", () => {
    expectAA("#1A1917", "#EFEBE0", "light close hover");
  });
});

describe("usage popover — refresh icon (3:1 UI-component bar)", () => {
  it("icons clear 3:1 in both themes and both states", () => {
    expectIcon("#8A8578", DARK_BG, "dark refresh default");
    expectIcon("#E8B94D", DARK_BG, "dark refresh loading");
    expectIcon("#716C60", LIGHT_BG, "light refresh default");
    expectIcon("#8A6523", LIGHT_BG, "light refresh loading");
  });
});