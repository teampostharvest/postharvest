import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/**
 * Skeleton — plans/ui.md §7.6. Rounded --radius-sm blocks at --bg-subtle with
 * a slow shimmer. Replaces every literal `–` / `...` loading placeholder.
 * The shimmer falls back to a static block under prefers-reduced-motion.
 */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("skeleton-block", className)} {...props} />;
}

export interface SkeletonTextProps {
  /** Number of lines (each narrower than the last). */
  lines?: number;
  className?: string;
}

export function SkeletonText({ lines = 3, className }: SkeletonTextProps) {
  return (
    <div className={cn("space-y-2", className)} role="status" aria-label="Loading">
      {Array.from({ length: lines }).map((_, index) => (
        <div
          key={index}
          className="skeleton-block h-3.5"
          style={{ width: lines === 1 ? "100%" : `${Math.max(40, 100 - index * 18)}%` }}
        />
      ))}
      <span className="sr-only">Loading</span>
    </div>
  );
}