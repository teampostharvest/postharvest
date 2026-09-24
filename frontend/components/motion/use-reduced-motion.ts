"use client";

import { useEffect, useState } from "react";

/**
 * Reduced-motion gate (plans/ui.md §6.1): every animation gets a reduced
 * variant — opacity-only or none at all — driven by prefers-reduced-motion.
 * Wire animations through this hook so components never have to remember.
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return reduced;
}