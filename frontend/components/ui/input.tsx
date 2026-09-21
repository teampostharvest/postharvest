import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/**
 * Input — plans/ui.md §7.3. Elevated fill, strong hairline border, base-size
 * type; focus swaps the border to the accent with a soft 3px ring at 15%.
 */
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, type = "text", ...props },
  ref
) {
  return (
    <input
      ref={ref}
      type={type}
      className={cn(
        "flex h-9 w-full rounded-sm border border-border-strong bg-bg-elevated px-3 py-1.5 text-base text-ink transition-colors duration-150 placeholder:text-ink-faint focus-visible:border-accent focus-visible:outline-hidden focus-visible:ring-[3px] focus-visible:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    />
  );
});

Input.displayName = "Input";