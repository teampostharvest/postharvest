"use client";

import { useEffect, useState } from "react";
import { AlertCircle, Check, RefreshCw, ShieldCheck } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { useTheme } from "@/components/common/ThemeProvider";
import { AccountCard } from "@/components/common/AccountCard";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton, SkeletonText } from "@/components/ui/skeleton";
import {
  readScrapeDefaults,
  writeScrapeDefaults,
  type ScrapeDefaults,
} from "@/lib/settings";
import type { AdminUser } from "@/lib/types";
import { PLAN_LABELS } from "@/lib/types";
import { cn } from "@/lib/utils";

const POST_TYPE_OPTIONS: ReadonlyArray<{ value: ScrapeDefaults["postType"]; label: string }> = [
  { value: "", label: "All post types" },
  { value: "text", label: "Text" },
  { value: "image", label: "Image" },
  { value: "video", label: "Video / Reel" },
  { value: "link", label: "Link" },
];

export default function SettingsPage() {
  const { theme, setTheme } = useTheme();
  const { profile } = useAuth();
  const isOps = profile?.role === "ops";
  const [defaults, setDefaults] = useState<ScrapeDefaults | null>(null);
  const [saved, setSaved] = useState(false);

  // --- operator administration ----------------------------------------------
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [adminError, setAdminError] = useState<string | null>(null);
  const [updating, setUpdating] = useState<string | null>(null);

  const loadUsers = async () => {
    setUsersLoading(true);
    setAdminError(null);
    try {
      setUsers(await api.adminListUsers());
    } catch (e) {
      setAdminError(e instanceof Error ? e.message : "Could not load users");
    } finally {
      setUsersLoading(false);
    }
  };

  useEffect(() => {
    if (isOps) void loadUsers();
  }, [isOps]);

  const changeField = async (user: AdminUser, field: "role" | "plan", value: string) => {
    setUpdating(`${user.id}:${field}`);
    setAdminError(null);
    try {
      const updated =
        field === "role" ? await api.adminSetRole(user.id, value) : await api.adminSetPlan(user.id, value);
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
    } catch (e) {
      setAdminError(e instanceof Error ? e.message : `Could not update ${field}`);
    } finally {
      setUpdating(null);
    }
  };

  useEffect(() => {
    setDefaults(readScrapeDefaults());
  }, []);

  const update = (patch: Partial<ScrapeDefaults>) => {
    if (!defaults) return;
    const next = { ...defaults, ...patch };
    setDefaults(next);
    writeScrapeDefaults(next);
    setSaved(true);
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Appearance</CardTitle>
          <CardDescription className="mt-1">Dark is the terminal default; light is fully supported.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2">
            {(
              [
                { value: "dark", label: "Dark" },
                { value: "light", label: "Light" },
              ] as const
            ).map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setTheme(option.value)}
                aria-pressed={theme === option.value}
                className={cn(
                  "flex items-center gap-2 rounded-sm border px-3 py-1.5 text-sm transition-colors",
                  theme === option.value
                    ? "border-ink bg-ink text-bg"
                    : "border-border-strong text-ink-muted hover:bg-bg-subtle",
                )}
              >
                {theme === option.value ? <Check className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" /> : null}
                {option.label}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Default scrape options</CardTitle>
          <CardDescription className="mt-1">
            Prefills the "New scrape" form. Stored locally in this browser; overridable per run.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {defaults === null ? (
            <div role="status" aria-label="Loading defaults">
              <SkeletonText lines={3} />
            </div>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div className="space-y-1.5">
                  <label htmlFor="set-max-posts" className="text-sm font-medium">Maximum posts</label>
                  <Input
                    id="set-max-posts"
                    type="number"
                    min={1}
                    step={1}
                    value={defaults.maxPosts}
                    placeholder="No limit"
                    onChange={(event) => update({ maxPosts: event.target.value })}
                  />
                </div>
                <div className="space-y-1.5">
                  <label htmlFor="set-post-type" className="text-sm font-medium">Post type</label>
                  <Select
                    id="set-post-type"
                    value={defaults.postType}
                    onChange={(event) => update({ postType: event.target.value as ScrapeDefaults["postType"] })}
                  >
                    {POST_TYPE_OPTIONS.map((option) => (
                      <option key={option.value || "all"} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <label htmlFor="set-scrolls" className="text-sm font-medium">Scroll rounds</label>
                  <Input
                    id="set-scrolls"
                    type="number"
                    min={1}
                    max={300}
                    step={1}
                    value={defaults.scrolls}
                    placeholder="40"
                    onChange={(event) => update({ scrolls: event.target.value })}
                  />
                </div>
              </div>

              <label className="flex w-fit cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  checked={defaults.useBrowser}
                  onChange={(event) => update({ useBrowser: event.target.checked })}
                  className="mt-0.5 h-4 w-4 accent-ink"
                />
                <span className="space-y-1">
                  <span className="block text-sm font-medium">Browser mode by default</span>
                  <span className="block text-xs text-ink-muted">
                    Scrape the page's own GraphQL feed via Playwright instead of the static HTML fallback.
                  </span>
                </span>
              </label>

              <p className="text-xs text-ink-muted">
                {saved ? "Saved ✓" : "Changes save immediately."}
              </p>
            </>
          )}
        </CardContent>
      </Card>

      {isOps ? (
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle>Administration</CardTitle>
              <CardDescription className="mt-1">
                Operators manage user roles and subscription tiers here. Roles gate operator features; tiers set
                per-user scrape limits.
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              {adminError ? (
                <span className="flex items-center gap-1.5 rounded-sm border border-danger/40 bg-danger/10 px-2 py-1 text-xs text-danger">
                  <AlertCircle className="h-3 w-3" aria-hidden="true" />
                  {adminError}
                </span>
              ) : null}
              <button
                type="button"
                onClick={() => void loadUsers()}
                disabled={usersLoading}
                className="flex items-center gap-1.5 rounded-sm border border-border-strong px-2 py-1 text-xs text-ink-muted transition-colors hover:text-ink disabled:opacity-50"
              >
                <RefreshCw className="h-3 w-3" strokeWidth={1.75} /> Refresh
              </button>
            </div>
          </CardHeader>
          <CardContent>
            {usersLoading && users.length === 0 ? (
              <div className="py-4" role="status" aria-label="Loading users">
                <Skeleton className="h-9 w-full" />
                <Skeleton className="mt-2 h-9 w-full" />
                <Skeleton className="mt-2 h-9 w-full" />
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-xs font-medium text-ink-muted">
                      <th className="px-2 py-2 font-medium">User</th>
                      <th className="px-2 py-2 font-medium">Role</th>
                      <th className="px-2 py-2 font-medium">Plan</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {users.map((user) => {
                      const busy = updating === `${user.id}:role` || updating === `${user.id}:plan`;
                      return (
                        <tr key={user.id}>
                          <td className="px-2 py-2.5">
                            <p className="font-medium">{user.display_name || user.email || `User #${user.id}`}</p>
                            <p className="flex items-center gap-1.5 text-xs text-ink-muted">
                              <span>{user.email ?? "no email"}</span>
                              {user.is_active ? null : (
                                <span className="rounded-sm bg-bg-subtle px-1.5 py-0.5 font-medium text-ink-muted">
                                  disabled
                                </span>
                              )}
                            </p>
                          </td>
                          <td className="px-2 py-2.5">
                            <Select
                              value={user.role}
                              disabled={busy}
                              aria-label={`Role for ${user.email ?? user.id}`}
                              onChange={(event) => void changeField(user, "role", event.target.value)}
                              className="h-8 w-32"
                            >
                              <option value="user">user</option>
                              <option value="ops">ops</option>
                            </Select>
                          </td>
                          <td className="px-2 py-2.5">
                            <Select
                              value={user.plan}
                              disabled={busy}
                              aria-label={`Plan for ${user.email ?? user.id}`}
                              onChange={(event) => void changeField(user, "plan", event.target.value)}
                              className="h-8 w-40"
                            >
                              {Object.entries(PLAN_LABELS).map(([value, label]) => (
                                <option key={value} value={value}>
                                  {label}
                                </option>
                              ))}
                            </Select>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <p className="mt-3 flex items-center gap-1.5 text-xs text-ink-muted">
              <ShieldCheck className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              Tiers: Basic / Pro / Team / Enterprise. You can't demote yourself.
            </p>
          </CardContent>
        </Card>
      ) : null}

      <AccountCard />
    </div>
  );
}