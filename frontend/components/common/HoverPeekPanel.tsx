"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { OpsSidebar } from "@/components/common/OpsSidebar";

const OPEN_DELAY_MS = 120;
const CLOSE_DELAY_MS = 180;

/**
 * Hover-to-peek panel for the collapsed state (mirrors the reference
 * sidebar peek-popover): while the persisted state is collapsed, hovering
 * the left edge of the content row slides the full ops panel in as an
 * overlay that stays usable as long as the pointer is over it. Leaving
 * dismisses it after a short grace delay; Escape dismisses immediately.
 * The persisted collapsed state never changes — only the toggle flips it.
 */
export function HoverPeekPanel() {
  const [peek, setPeek] = useState(false);
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimers = () => {
    if (openTimer.current) clearTimeout(openTimer.current);
    if (closeTimer.current) clearTimeout(closeTimer.current);
    openTimer.current = null;
    closeTimer.current = null;
  };

  useEffect(() => () => clearTimers(), []);

  const scheduleOpen = useCallback(() => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
    if (openTimer.current) return;
    openTimer.current = setTimeout(() => {
      openTimer.current = null;
      setPeek(true);
    }, OPEN_DELAY_MS);
  }, []);

  const scheduleClose = useCallback(() => {
    if (openTimer.current) clearTimeout(openTimer.current);
    openTimer.current = null;
    if (closeTimer.current) return;
    closeTimer.current = setTimeout(() => {
      closeTimer.current = null;
      setPeek(false);
    }, CLOSE_DELAY_MS);
  }, []);

  const cancelClose = useCallback(() => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
    setPeek(true);
  }, []);

  useEffect(() => {
    if (!peek) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        clearTimers();
        setPeek(false);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [peek ]);

  return (
    <>
      {/* Hover strip at the content row's left edge (desktop only). */}
      <div
        aria-hidden="true"
        data-testid="peek-zone"
        onMouseEnter={scheduleOpen}
        className="absolute inset-y-0 left-0 z-30 hidden w-3 lg:block"
      />
      <AnimatePresence>
        {peek ? (
          <motion.aside
            key="peek-panel"
            aria-label="Operations panel preview"
            initial={{ x: -24, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: -16, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            onMouseEnter={cancelClose}
            onMouseLeave={scheduleClose}
            className="absolute bottom-0 left-0 top-0 z-40 w-[264px] shadow-lg"
          >
            <OpsSidebar />
          </motion.aside>
        ) : null}
      </AnimatePresence>
    </>
  );
}
