"use client";

import { useEffect, useState, type HTMLAttributes, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./button";

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  description?: string;
  children?: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  className?: string;
}

const sizeClasses: Record<NonNullable<DialogProps["size"]>, string> = {
  sm: "sm:max-w-sm",
  md: "sm:max-w-md",
  lg: "sm:max-w-2xl",
  xl: "sm:max-w-4xl",
};

/**
 * Lightweight accessible modal (no Radix dependency).
 * Closes on ESC or backdrop click; locks body scroll while open.
 */
export function Dialog({ open, onClose, title, description, children, footer, size = "md", className }: DialogProps) {
  // Render into a portal on <body> so no ancestor can hijack `position: fixed`
  // (a retained animation transform on the page container would otherwise
  // become the dialog's containing block and break centering).
  const [isClient, setIsClient] = useState(false);
  useEffect(() => setIsClient(true), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open || !isClient) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ display: "flex", alignItems: "center", justifyContent: "center" }}
      role="dialog"
      aria-modal="true"
      aria-label={title ?? "Dialog"}
    >
      <div
        className="absolute inset-0 bg-black/50 animate-fade-in"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        className={cn(
          "relative z-10 flex max-h-[86dvh] w-full flex-col overflow-hidden rounded-t-lg border border-border bg-popover text-popover-foreground shadow-lg animate-dialog-in sm:rounded-lg",
          sizeClasses[size],
          className
        )}
      >
        {title || description ? (
          <div className="flex items-start justify-between gap-4 border-b px-6 py-4">
            <div className="min-w-0">
              {title ? <h2 className="text-lg font-semibold leading-6">{title}</h2> : null}
              {description ? <p className="mt-0.5 text-sm text-muted-foreground">{description}</p> : null}
            </div>
            <Button variant="ghost" size="icon" className="-mr-2 -mt-1 shrink-0" onClick={onClose} aria-label="Close dialog">
              <X className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
        ) : (
          <div className="absolute right-4 top-4 z-10">
            <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close dialog">
              <X className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
        )}
        <div className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
        {footer ? <div className="border-t px-6 py-3">{footer}</div> : null}
      </div>
    </div>,
    document.body
  );
}

export function DialogSection({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <section className={cn("space-y-2 not-prose", className)} {...props} />;
}