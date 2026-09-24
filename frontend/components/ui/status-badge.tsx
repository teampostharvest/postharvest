import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/**
 * StatusBadge — plans/ui.md §7.4. Pill shape, --text-xs/500, semantic color
 * pairs, small leading dot. The label always carries the tone as well as the
 * dot (status is never conveyed by color alone). Pass `pulse` for live
 * "running"-style states.
 */
export type StatusBadgeTone = "neutral" | "success" | "danger" | "warning" | "brand";

export interface StatusBadgeProps extends HTMLAttributes<HTMLSpanElement> {
  children: React.ReactNode;
  tone?: StatusBadgeTone;
  /** Gently pulses the dot (reserved for genuinely in-progress states). */
  pulse?: boolean;
}

const toneClasses: Record<StatusBadgeTone, string> = {
  neutral: "border-border bg-bg-subtle text-ink-muted",
  success: "border-border bg-success-bg text-success",
  danger: "border-border bg-danger-bg text-danger",
  warning: "border-border bg-warning-bg text-warning",
  brand: "border-border-strong bg-bg-subtle text-ink",
};

const dotClasses: Record<StatusBadgeTone, string> = {
  neutral: "bg-ink-faint",
  success: "bg-success",
  danger: "bg-danger",
  warning: "bg-warning",
  brand: "bg-accent",
};

export function StatusBadge({
  children,
  tone = "neutral",
  pulse = false,
  className,
  ...props
}: StatusBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium leading-4",
        toneClasses[tone],
        className
      )}
      {...props}
    >
      <span
        className={cn("h-2 w-2 shrink-0 rounded-full", dotClasses[tone], pulse && "animate-pulse-dot")}
        aria-hidden="true"
      />
      <span>{children}</span>
    </span>
  );
}