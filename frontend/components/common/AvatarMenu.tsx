"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { MotionIcon } from "motion-icons-react";
import { useAuth } from "@/lib/auth-context";
import { PLAN_LABELS } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Account menu in the top bar (avatar + chevron trigger).
 *
 * Identity block (name / email / plan, read-only), then Saved accounts and
 * Settings (full pages, re-entered from here), then Sign out. Closes on
 * outside click and Escape.
 */
export function AvatarMenu() {
  const { user, profile, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    // Move focus into the menu on open (WAI menu-button pattern).
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        // Return focus to the trigger so keyboard users don't lose place.
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open ]);

  if (!user) {
    return null;
  }

  const name = profile?.display_name || user.email?.split("@")[0] || "Account";
  const email = profile?.email || user.email || "";
  const planLabel = PLAN_LABELS[profile?.plan ?? ""] ?? "Basic";
  const initial = name.charAt(0).toUpperCase();

  const itemClass =
    "flex w-full items-center gap-2.5 px-4 py-2 text-sm font-medium text-ink-muted transition-colors hover:bg-bg-subtle hover:text-ink";

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        ref={triggerRef}
        onClick={() => setOpen((current) => !current)}
        aria-label="Account menu"
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex items-center gap-1.5 text-ink-muted transition-colors hover:text-ink"
      >
        {profile?.photo_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={profile.photo_url}
            alt=""
            aria-hidden="true"
            className="h-7 w-7 rounded-full border border-border object-cover"
          />
        ) : (
          <span
            aria-hidden="true"
            className="flex h-7 w-7 items-center justify-center rounded-full border border-border bg-bg-subtle font-sans text-xs font-medium text-ink-muted"
          >
            {initial}
          </span>
        )}
        <MotionIcon
          name="ChevronDown"
          size={14}
          aria-hidden="true"
          animation="nudge"
          trigger="hover"
          className={cn("transition-transform duration-200", open && "rotate-180")}
        />
      </button>

      {open ? (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Account"
          className="absolute right-0 top-full z-50 mt-2 w-60 rounded-md bg-bg-elevated py-1 shadow-lg"
        >
          <div className="border-b border-border px-4 py-3">
            <p className="truncate font-sans text-sm font-medium text-ink">{name}</p>
            {email ? (
              <p className="mt-0.5 truncate font-mono text-[11px] text-ink-muted">{email}</p>
            ) : null}
            <p className="mt-1.5 text-xs font-medium text-highlight">
              {planLabel} plan
            </p>
          </div>
          <div className="border-b border-border py-1" role="none">
            <Link href="/accounts" role="menuitem" onClick={() => setOpen(false)} className={itemClass}>
              <MotionIcon name="KeyRound" size={16} aria-hidden="true" animation="wiggle" trigger="hover" />
              Saved accounts
            </Link>
            <Link href="/settings" role="menuitem" onClick={() => setOpen(false)} className={itemClass}>
              <MotionIcon name="Settings" size={16} aria-hidden="true" animation="spin" trigger="hover" />
              Settings
            </Link>
          </div>
          <div className="py-1" role="none">
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                void signOut();
              }}
              className={itemClass}
            >
              <MotionIcon name="LogOut" size={16} aria-hidden="true" animation="nudge" trigger="hover" />
              Sign out
            </button>
          </div>
          {!profile ? (
            <p className="border-t border-border px-4 py-2 text-xs text-ink-muted">
              <MotionIcon name="UserRound" size={12} aria-hidden="true" className="mr-1 inline-flex align-[-1px]" />
              Profile syncing…
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
