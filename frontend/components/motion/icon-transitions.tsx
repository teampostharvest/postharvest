"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Check, Loader2, Play, RotateCw } from "lucide-react";
import { useReducedMotion } from "./use-reduced-motion";

export type LaunchStage = "idle" | "loading" | "success";

/**
 * Play → Loader → Check morph for the launch CTA (plans/ui.md §6.3).
 * The scrape lifecycle is the product's one place for a slightly more
 * expressive animation: the submission switcher. 160–200ms cross-fade with
 * a tight scale; reduced-motion users get an instant swap.
 */
export function LaunchIcon({ stage, size = 16 }: { stage: LaunchStage; size?: number }) {
  const reduce = useReducedMotion();

  if (reduce) {
    return (
      <span className="inline-flex" aria-hidden="true">
        {stage === "loading" ? (
          <Loader2 size={size} className="animate-spin" />
        ) : stage === "success" ? (
          <Check size={size} />
        ) : (
          <Play size={size} className="translate-x-px" />
        )}
      </span>
    );
  }

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.span
        key={stage}
        initial={{ opacity: 0, scale: 0.6, rotate: stage === "success" ? -90 : 0 }}
        animate={{ opacity: 1, scale: 1, rotate: 0 }}
        exit={{ opacity: 0, scale: 0.6, rotate: 90 }}
        transition={{ duration: 0.18, ease: "easeOut" }}
        className="inline-flex"
        aria-hidden="true"
      >
        {stage === "loading" ? (
          <Loader2 size={size} className="animate-spin" />
        ) : stage === "success" ? (
          <Check size={size} />
        ) : (
          <Play size={size} className="translate-x-px" />
        )}
      </motion.span>
    </AnimatePresence>
  );
}

/**
 * Wraps a spinner rotation around a retry action (plans/ui.md §6.3 — the
 * RotateCw "spin-on-click"). Re-trigger by incrementing `spinToken`; the
 * remount replays the 360° sweep. Reduced-motion users get a static icon.
 */
export function SpinOnClick({ spinToken = 0, size = 16 }: { spinToken?: number; size?: number }) {
  const reduce = useReducedMotion();

  if (reduce) {
    return (
      <RotateCw key={`static-${spinToken}`} size={size} aria-hidden="true" className="shrink-0" />
    );
  }

  return (
    <motion.span
      key={spinToken}
      initial={{ rotate: 0 }}
      animate={{ rotate: 360 }}
      transition={{ duration: 0.5, ease: "easeOut" }}
      className="inline-flex shrink-0"
      aria-hidden="true"
    >
      <RotateCw size={size} />
    </motion.span>
  );
}