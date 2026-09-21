import localFont from "next/font/local";
import { JetBrains_Mono } from "next/font/google";

/**
 * Self-hosted type system (plans/ui.md §3.1, §12.1):
 *  - Satoshi        → display (page titles, hero numbers, login headline)
 *  - General Sans   → UI / body (the workhorse — 90% of on-screen text)
 *  - JetBrains Mono → technical data only (IDs, URLs, timestamps, counts)
 *
 * The generics font names remain mapped in `app/globals.css` `@theme` via
 * `--font-sans` / `--font-display` / `--font-mono`.
 */
export const satoshi = localFont({
  src: [
    { path: "../public/fonts/Satoshi-Medium.woff2", weight: "500", style: "normal" },
    { path: "../public/fonts/Satoshi-Bold.woff2", weight: "700", style: "normal" },
  ],
  variable: "--font-satoshi",
  display: "swap",
});

export const generalSans = localFont({
  src: [
    { path: "../public/fonts/GeneralSans-Regular.woff2", weight: "400", style: "normal" },
    { path: "../public/fonts/GeneralSans-Medium.woff2", weight: "500", style: "normal" },
    { path: "../public/fonts/GeneralSans-Semibold.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-general-sans",
  display: "swap",
});

export const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});