"use client";

import React from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import { PLAN_LABELS } from "@/lib/types";
import { CreditCard, LogOut, User as UserIcon } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

/**
 * Account card shown at the bottom of the Settings page.
 *
 * The signed-in identity and sign-out live where account actions belong —
 * on the Settings page itself (also reachable from the top-bar avatar menu).
 */
export function AccountCard() {
  const { user, profile, signOut } = useAuth();

  // Signed-out visitors never reach the app shell, but stay defensive.
  if (!user) {
    return null;
  }

  const name = profile?.display_name || user.email?.split("@")[0] || "User";
  const planLabel = PLAN_LABELS[profile?.plan ?? ""] ?? "Free";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Account</CardTitle>
        <CardDescription className="mt-1">
          The signed-in identity and plan attached to this session.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-border bg-bg-subtle text-ink-muted">
              <UserIcon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-ink">{name}</p>
              <p className="mt-0.5 text-xs font-medium text-highlight">{planLabel} plan</p>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <Link href="/pricing">
              <Button variant="primary" size="sm">
                <CreditCard className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                Change plan
              </Button>
            </Link>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => void signOut()}
              aria-label="Sign out"
              title="Sign out"
            >
              <LogOut className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              Sign out
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}