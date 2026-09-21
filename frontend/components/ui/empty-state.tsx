import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * EmptyState — plans/ui.md §7.5. Centered within its container with generous
 * vertical padding, a large outline icon in --ink-faint, sentence-case copy,
 * and at most one secondary/ghost action. Never a full-width bordered box.
 */
export interface EmptyStateProps {
  icon?: ReactNode;
  title?: ReactNode;
  description?: ReactNode;
  /** A single secondary/ghost Button resolving the emptiness. */
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 px-6 py-16 text-center sm:py-20",
        className
      )}
      aria-label="Empty"
    >
      {icon ? (
        <div className="text-ink-faint" aria-hidden="true">
          {icon}
        </div>
      ) : null}
      {title ? <p className="max-w-md text-md font-medium text-ink-muted">{title}</p> : null}
      {description ? <p className="max-w-sm text-sm leading-relaxed text-ink-muted">{description}</p> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}