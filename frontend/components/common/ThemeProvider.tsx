"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { DEFAULT_THEME_ID, THEMES, getTheme, isThemeId, nextThemeId } from "@/lib/themes";

type Theme = string;

interface ThemeContextValue {
  theme: Theme;
  /** All registered themes (for settings/menus — the registry owns the list). */
  themes: typeof THEMES;
  setTheme: (theme: Theme) => void;
  /** Cycle to the next registered theme. */
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);
const STORAGE_KEY = "postharvest-theme";

function readStoredTheme(): Theme | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored && isThemeId(stored)) return stored;
  } catch {
    // localStorage unavailable (private mode) — fall through to the default.
  }
  return null;
}

function applyThemeClass(themeId: string): void {
  const root = document.documentElement;
  for (const entry of THEMES) {
    if (entry.className) root.classList.remove(entry.className);
  }
  const def = getTheme(themeId);
  if (def.className) root.classList.add(def.className);
}

/**
 * Class-strategy theme provider: applies the registered theme's class to
 * <html> and persists the choice to localStorage. Colors are plain CSS
 * variables (see globals.css). Light-first: the warm canvas is the default;
 * dark is an opt-in terminal look. New themes plug into lib/themes.ts.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(DEFAULT_THEME_ID);

  // Apply the stored choice right after hydration (default theme first).
  useEffect(() => {
    setThemeState(readStoredTheme() ?? DEFAULT_THEME_ID);
  }, []);

  useEffect(() => {
    applyThemeClass(theme);
    const def = getTheme(theme);
    if (def.id === "light" || def.id === "dark") {
      document.documentElement.style.colorScheme = def.id;
    }
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Ignore write failures (private mode).
    }
  }, [theme]);

  const setTheme = useCallback(
    (next: Theme) => setThemeState(getTheme(next).id),
    []
  );
  const toggleTheme = useCallback(() => setThemeState((current) => nextThemeId(current)), []);

  return (
    <ThemeContext.Provider value={{ theme, themes: THEMES, setTheme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used within a ThemeProvider");
  return context;
}
