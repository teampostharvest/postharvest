"use client";

import type { ComponentType } from "react";
import {
  CreditCard,
  History,
  Home,
  KeyRound,
  ScanLine,
  Settings,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: ComponentType<{ className?: string; strokeWidth?: number | string }>;
  /** Shown in the top-bar link row as well as the sidebar. */
  topBar: boolean;
}

/**
 * Single source of truth for primary navigation. The sidebar's
 * active-item highlighting, the top-bar links, and the dynamic section
 * header (`getActiveLabel`) all read from this config so they can never
 * drift out of sync.
 */
export const NAV_ITEMS: NavItem[] = [
  // NOTE: Home stays out of the top-bar row on purpose — the wordmark
  // logo already links to "/" and a second Home link is a duplicate.
  { href: "/", label: "Home", icon: Home, topBar: false },
  { href: "/investigation", label: "Investigation", icon: ScanLine, topBar: true },
  { href: "/pricing", label: "Pricing", icon: CreditCard, topBar: true },
  { href: "/history", label: "History", icon: History, topBar: true },
  { href: "/accounts", label: "Saved accounts", icon: KeyRound, topBar: false },
  { href: "/settings", label: "Settings", icon: Settings, topBar: false },
];

export function isDocsPath(pathname: string): boolean {
  return pathname === "/docs" || pathname.startsWith("/docs/");
}

/** True when `pathname` falls under the nav item's route. */
export function isActiveHref(href: string, pathname: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * Display label for the current section header + sidebar highlight,
 * derived from the route — never a static string.
 */
export function getActiveLabel(pathname: string): string {
  if (isDocsPath(pathname)) return "Docs";
  const match = NAV_ITEMS.find((item) => isActiveHref(item.href, pathname));
  return match?.label ?? "PostHarvest";
}
