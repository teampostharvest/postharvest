import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export type BadgeVariant =
  | "default"
  | "secondary"
  | "outline"
  | "destructive"
  | "success"
  | "warning"
  | "blue"
  | "purple"
  | "amber";

/** Small tag/flag chips (grid badges, tier markers). For job/account status
 *  indicators use `StatusBadge`. Kept on the palette via the canonical tokens. */
const variantClasses: Record<BadgeVariant, string> = {
  default: "border-border bg-secondary text-secondary-foreground",
  secondary: "border-border bg-muted text-muted-foreground",
  outline: "border-border text-muted-foreground bg-transparent",
  destructive: "bg-foreground text-background border-transparent",
  success: "border-border bg-success-bg text-success",
  warning: "border-border bg-warning-bg text-warning",
  blue: "border-border bg-bg-subtle text-ink-muted",
  purple: "border-border bg-bg-subtle text-ink-muted",
  amber: "border-border bg-bg-subtle text-ink-muted",
};

export function Badge({
  className,
  variant = "default",
  ...props
}: HTMLAttributes<HTMLSpanElement> & { variant?: BadgeVariant }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs font-medium transition-colors",
        variantClasses[variant],
        className
      )}
      {...props}
    />
  );
}