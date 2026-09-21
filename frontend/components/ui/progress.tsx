import { cn } from "@/lib/utils";

export interface ProgressProps {
  /** 0..max; null/undefined renders an indeterminate animated bar. */
  value?: number | null;
  max?: number;
  indeterminate?: boolean;
  className?: string;
  indicatorClassName?: string;
}

export function Progress({ value, max = 100, indeterminate = false, className, indicatorClassName }: ProgressProps) {
  const hasValue = value != null && Number.isFinite(value);
  const percent = hasValue ? Math.max(0, Math.min(100, (value / Math.max(1, max)) * 100)) : 0;
  const isIndeterminate = indeterminate || !hasValue;

  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={isIndeterminate ? undefined : Math.round(percent)}
      aria-label="Job progress"
      className={cn("relative h-2.5 w-full overflow-hidden rounded-full bg-bg-subtle", className)}
    >
      <div
        className={cn(
          "h-full rounded-full bg-accent transition-[width] duration-500 ease-out",
          isIndeterminate ? "w-1/3 animate-indeterminate" : "progress-stripes",
          indicatorClassName
        )}
        style={isIndeterminate ? undefined : { width: `${percent}%` }}
      />
    </div>
  );
}