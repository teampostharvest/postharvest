import { forwardRef, type ButtonHTMLAttributes } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Button — plans/ui.md §7.1.
 * Variants: primary | secondary | ghost | destructive (+ outline/link for
 * legacy callers). Sizes: sm 32px | md 40px | lg 48px.
 * One accent color per screen: `primary` is the only gold button anywhere.
 */
type Variant = "default" | "primary" | "secondary" | "outline" | "ghost" | "destructive" | "link";
type Size = "default" | "sm" | "md" | "lg" | "icon";

const base =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-sm text-sm font-medium transition-colors duration-150 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0";

const variants: Record<Variant, string> = {
  primary: "bg-accent text-accent-ink hover:bg-accent-hover",
  default: "bg-accent text-accent-ink hover:bg-accent-hover",
  secondary: "border border-border-strong bg-bg-elevated text-ink hover:bg-bg-subtle",
  outline: "border border-border-strong bg-bg-elevated text-ink hover:bg-bg-subtle",
  ghost: "text-ink-muted hover:bg-bg-subtle hover:text-ink",
  destructive: "border border-danger bg-destructive text-destructive-foreground hover:bg-destructive/80",
  link: "text-ink underline-offset-4 hover:text-accent hover:underline",
};

const sizes: Record<Size, string> = {
  default: "h-10 px-4",
  sm: "h-8 px-3",
  md: "h-10 px-4",
  lg: "h-12 px-6",
  icon: "h-9 w-9",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "default", size = "md", loading = false, disabled, children, ...props },
  ref
) {
  return (
    <button
      ref={ref}
      className={cn(base, variants[variant], sizes[size], className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
      {children}
    </button>
  );
});