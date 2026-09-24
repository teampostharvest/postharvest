"use client";

import { cn } from "@/lib/utils";
import { LaunchIcon, type LaunchStage } from "@/components/motion/icon-transitions";

export interface LaunchButtonProps {
  onClick: () => void;
  disabled?: boolean;
  loading?: boolean;
  /** Set once a run was accepted to morph Loader → Check (plans/ui.md §6.3). */
  success?: boolean;
  /** Idle label, e.g. "Start scraper". */
  children: React.ReactNode;
  /** Shown while loading, e.g. "Starting…". Defaults to children. */
  loadingLabel?: React.ReactNode;
  className?: string;
  ariaLabel?: string;
}

/**
 * Primary launch CTA: accent pill (the single gold button per screen per
 * §7.1), Play → Loader → Check morph while a run is submitted, sheen sweep
 * on hover, press squash. One shared shape for every "start a run" button
 * (Investigation, Dashboard, Home).
 */
export function LaunchButton({
  onClick,
  disabled = false,
  loading = false,
  success = false,
  children,
  loadingLabel,
  className,
  ariaLabel,
}: LaunchButtonProps) {
  const busy = disabled || loading;
  const stage: LaunchStage = success ? "success" : loading ? "loading" : "idle";

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-label={ariaLabel}
      className={cn(
        "btn-sheen inline-flex h-12 items-center justify-center gap-2.5 whitespace-nowrap rounded-full",
        "bg-accent px-8 text-sm font-medium text-accent-ink",
        "transition-all duration-200 hover:bg-accent-hover hover:shadow-lg active:scale-[0.98]",
        "disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-accent disabled:hover:shadow-none disabled:active:scale-100",
        className,
      )}
    >
      <LaunchIcon stage={stage} size={16} />
      <span>{loading && loadingLabel ? loadingLabel : children}</span>
    </button>
  );
}