import type { HTMLAttributes, LabelHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * MailAccess display system.
 * Eyebrow/PageHeading/SectionHeading build the big mono-headed hero blocks;
 * StatBox is the flat footer-style label/value row. All tokens come from the
 * stylesheet, so the light theme renders the exact white/black/red spec.
 */
export function Eyebrow({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p
      className={cn(
        "mb-4 block text-xs font-semibold text-highlight",
        className
      )}
      {...props}
    />
  );
}

export function PageHeading({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h1
      className={cn(
        "font-display font-semibold tracking-tight leading-[1.04] text-4xl md:text-5xl text-ink mb-8",
        className
      )}
      {...props}
    />
  );
}

export function SectionHeading({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h2 className={cn("font-semibold tracking-tight text-lg text-ink mb-3", className)} {...props} />
  );
}

export interface StatBoxProps extends HTMLAttributes<HTMLDivElement> {
  label: ReactNode;
  value: ReactNode;
}

export function StatBox({ label, value, className, ...props }: StatBoxProps) {
  return (
    <div
      className={cn(
        "flex items-center justify-between py-3 border-b border-border text-xs text-ink-muted last:border-b-0",
        className
      )}
      {...props}
    >
      <span>{label}</span>
      <span className="font-mono tabular-nums text-ink">{value}</span>
    </div>
  );
}

export function FieldLabel({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn(
        "block text-sm font-medium text-ink-muted",
        className
      )}
      {...props}
    />
  );
}

/** Small bordered flat chip, used for counts/status inside grids. */
export function Chip({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 border border-border px-2 py-1 text-xs font-medium text-ink-muted",
        className
      )}
      {...props}
    />
  );
}