"use client";

/**
 * Extensible theme registry. Themes are data, not branches: adding a new
 * theme means appending one entry here plus a matching CSS scope in
 * `app/globals.css` (e.g. `.theme-sepia { --shell-content: ...; }`).
 * Nothing else in the app switches on theme names.
 */

export interface ThemeDef {
  /** Stable id persisted to localStorage. */
  id: string;
  /** Human label for settings/menus. */
  label: string;
  /** Class applied to <html> ("" = default, no class). */
  className: string;
}

export const THEMES: ThemeDef[] = [
  { id: "light", label: "Light", className: "" },
  { id: "dark", label: "Dark", className: "dark" },
];

export const DEFAULT_THEME_ID = "light";

export function getTheme(id: string | null | undefined): ThemeDef {
  return THEMES.find((theme) => theme.id === id) ?? THEMES[0];
}

export function isThemeId(value: string | null | undefined): boolean {
  return THEMES.some((theme) => theme.id === value);
}

/** Next theme in registry order (the toggle cycles through all of them). */
export function nextThemeId(currentId: string): string {
  const index = THEMES.findIndex((theme) => theme.id === currentId);
  return THEMES[(index + 1) % THEMES.length].id;
}
