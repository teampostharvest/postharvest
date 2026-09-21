"use client";

import { useCallback, useEffect, useState } from "react";
import { ExternalLink, KeyRound, Loader2, Plus, RefreshCw, Trash2, Users } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { AccountSession, SessionCaptureOut } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogSection } from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

function StatusBadge({ status }: { status?: string | null }) {
  // Shared vocabulary with the sidebar legend: healthy / needs attention.
  // Unknown (never checked) is neutral — never mislabeled as expired.
  const state = status === "VALID" ? "healthy" : status === "EXPIRED" ? "attention" : "unknown";
  return (
    <span
      className={
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium " +
        (state === "healthy"
          ? "border-success/40 bg-success/10 text-success"
          : state === "attention"
            ? "border-warning/40 bg-warning/10 text-warning"
            : "border-border bg-bg-subtle/40 text-ink-muted")
      }
    >
      {state === "healthy" ? "healthy" : state === "attention" ? "needs attention" : "unknown"}
    </span>
  );
}

export default function AccountsPage() {
  const { profile } = useAuth();
  const isOps = profile?.role === "ops";

  const [ops, setOps] = useState<AccountSession[]>([]);
  const [mine, setMine] = useState<AccountSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  // Live session-capture flow (replaces the old email/password form — Facebook
  // shows a CAPTCHA on fresh logins, so the user signs in in a new tab instead).
  const [addOpen, setAddOpen] = useState(false);
  const [addScope, setAddScope] = useState<"me" | "ops">("me");
  const [addName, setAddName] = useState("");
  const [capture, setCapture] = useState<SessionCaptureOut | null>(null);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listAccounts();
      setOps(res.ops);
      setMine(res.mine);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load saved sessions");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleDelete = useCallback(
    async (scope: string, name: string) => {
      setDeleting(`${scope}/${name}`);
      setError(null);
      try {
        await api.deleteAccount(scope, name);
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : `Could not remove "${name}"`);
      } finally {
        setDeleting(null);
      }
    },
    [load],
  );

  const openAdd = (scope: "me" | "ops") => {
    setAddScope(scope);
    setAddName("");
    setCapture(null);
    setAdding(false);
    setAddError(null);
    setWaiting(false);
    setAddOpen(true);
  };

  const closeAdd = () => {
    setAddOpen(false);
    setAddError(null);
    setCapture(null);
    setWaiting(false);
  };

  const handleStartCapture = async () => {
    setAddError(null);
    setAdding(true);
    try {
      const info = await api.startSessionCapture({ name: addName.trim(), scope: addScope });
      setCapture(info);
      setWaiting(true);
    } catch (e) {
      setAddError(e instanceof Error ? e.message : "Could not start session capture");
    } finally {
      setAdding(false);
    }
  };

  const openLoginTab = () => {
    if (!capture) return;
    window.open(capture.url, "_blank", "noopener,noreferrer");
  };

  const handleCancelCapture = async () => {
    if (capture) {
      try {
        await api.cancelSessionCapture(capture.capture_id);
      } catch {
        // best-effort — the capture also expires server-side
      }
    }
    closeAdd();
  };

  // While a capture is waiting, poll the account list so the new session
  // appears as soon as the backend saves it.
  useEffect(() => {
    if (!waiting || !capture) return;
    const timer = setInterval(() => {
      void load();
    }, 3000);
    return () => clearInterval(timer);
  }, [waiting, capture, load]);

  // When the captured account lands in the list, the flow is complete.
  useEffect(() => {
    if (!waiting || !capture) return;
    const delivered = (addScope === "ops" ? ops : mine).some((a) => a.name === capture.name);
    if (delivered) {
      setCapture(null);
      setWaiting(false);
      setAddOpen(false);
    }
  }, [waiting, capture, mine, ops, addScope]);

  const total = ops.length + mine.length;

  const renderRow = (account: AccountSession) => (
    <li key={`${account.scope}/${account.name}`} className="flex items-center gap-4 py-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-sm border border-border text-ink-muted">
        <KeyRound className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 text-sm font-medium">
          {account.name}
          <StatusBadge status={account.status} />
        </p>
        <p className="text-xs text-ink-muted">
          {account.scope === "me" ? "Personal session" : "Operator pool"}
        </p>
        {account.saved_at ? (
          <p className="mt-0.5 text-xs text-ink-faint">Saved {formatDateTime(account.saved_at)}</p>
        ) : null}
      </div>
      {account.scope === "ops" && !isOps ? (
        <span className="text-xs font-medium text-ink-muted">managed</span>
      ) : (
        <button
          type="button"
          onClick={() => void handleDelete(account.scope, account.name)}
          disabled={deleting === `${account.scope}/${account.name}`}
          className="flex items-center gap-1.5 rounded-sm border border-border-strong px-2 py-1 text-xs font-medium text-ink-muted transition-colors hover:border-danger/40 hover:text-danger disabled:opacity-50"
        >
          <Trash2 className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
          {deleting === `${account.scope}/${account.name}` ? "Removing…" : "Remove"}
        </button>
      )}
    </li>
  );

  if (loading && total === 0) {
    return (
      <div className="flex flex-col items-center gap-3 py-12" role="status" aria-label="Loading sessions">
        <Skeleton className="h-10 w-full max-w-md" />
        <Skeleton className="h-10 w-full max-w-md" />
        <Skeleton className="h-10 w-full max-w-md" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>Saved sessions</CardTitle>
            <CardDescription className="mt-1">
              Cookie sessions that unlock the full Facebook feed for browser scrapes. Metadata only — cookie contents
              are never exposed.
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            {isOps ? (
              <Button type="button" variant="outline" onClick={() => openAdd("ops")}>
                <Users className="h-3.5 w-3.5" aria-hidden="true" />
                Add shared session
              </Button>
            ) : null}
            <Button type="button" onClick={() => openAdd("me")}>
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              Add my session
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {error ? (
            <div className="mb-4 flex items-center justify-between gap-3 rounded-sm border border-danger/30 bg-danger/10 px-3 py-2.5 text-sm">
              <span className="text-ink">{error}</span>
              <button
                type="button"
                onClick={() => void load()}
                className="flex items-center gap-1.5 rounded-sm border border-border px-2 py-1 text-xs transition-colors hover:text-ink"
              >
                <RefreshCw className="h-3 w-3" strokeWidth={1.75} /> Retry
              </button>
            </div>
          ) : null}

          {/* Operator pool */}
          <section>
            <div className="flex items-center gap-2 border-b border-border pb-2">
              <Users className="h-3.5 w-3.5 text-ink-muted" strokeWidth={1.75} aria-hidden="true" />
              <h3 className="text-xs font-medium text-ink-muted">Operator pool</h3>
              <span className="rounded-full bg-bg-subtle px-1.5 py-0.5 text-xs text-ink-muted">shared</span>
              <span className="ml-auto text-xs text-ink-muted">{ops.length}</span>
            </div>
            {ops.length === 0 ? (
              <EmptyState
                icon={<Users strokeWidth={1.5} className="h-10 w-10" />}
                title="No shared sessions yet"
                description={
                  <>
                    Operators can add one via Add shared session or the CLI:{" "}
                    <code className="rounded-sm bg-bg-subtle px-1.5 py-0.5 font-mono text-xs">
                      cli.py login --account NAME
                    </code>
                    .
                  </>
                }
              />
            ) : (
              <ul className="divide-y divide-border">{ops.map(renderRow)}</ul>
            )}
          </section>

          {/* Personal sessions */}
          <section className="mt-6">
            <div className="flex items-center gap-2 border-b border-border pb-2">
              <KeyRound className="h-3.5 w-3.5 text-ink-muted" strokeWidth={1.75} aria-hidden="true" />
              <h3 className="text-xs font-medium text-ink-muted">My sessions</h3>
              <span className="ml-auto text-xs text-ink-muted">{mine.length}</span>
            </div>
            {mine.length === 0 ? (
              <EmptyState
                icon={<KeyRound strokeWidth={1.5} className="h-10 w-10" />}
                title="No personal sessions yet"
                description="Add one to log into Facebook from here — the resulting cookies unlock the full feed for your scrapes only."
              />
            ) : (
              <ul className="divide-y divide-border">{mine.map(renderRow)}</ul>
            )}
          </section>
        </CardContent>
      </Card>

      <Dialog
        open={addOpen}
        onClose={() => void (waiting ? handleCancelCapture() : closeAdd())}
        title={addScope === "ops" ? "Add shared Facebook session" : "Add my Facebook session"}
        description={
          capture
            ? "A one-time login browser is ready — open it to see the live Facebook page and sign in there."
            : "You'll sign in to Facebook in a new tab on the live page (solving any CAPTCHA there) — only the resulting session cookies are stored with your account, never the password."
        }
        size="lg"
      >
        {!capture ? (
          <div className="space-y-4">
            <DialogSection>
              <div className="space-y-1.5">
                <label htmlFor="add-name" className="text-sm font-medium">
                  Session name
                </label>
                <Input
                  id="add-name"
                  value={addName}
                  onChange={(event) => setAddName(event.target.value)}
                  placeholder={addScope === "ops" ? "e.g. shared-prod" : "e.g. personal-a"}
                  required
                  autoFocus
                />
              </div>
              <p className="text-xs text-ink-muted">
                Opens Facebook&lsquo;s live login page in a one-time browser hosted by this app — the page and your
                clicks stream through the app&lsquo;s own connection, so it works from any device with no extra ports.
                You complete the sign-in; this app only captures the session cookie.
              </p>
              {addError ? (
                <p className="rounded-sm border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
                  {addError}
                </p>
              ) : null}
              <div className="flex justify-end gap-2 pt-1">
                <Button type="button" variant="outline" onClick={() => void closeAdd()}>
                  Cancel
                </Button>
                <Button type="button" onClick={() => void handleStartCapture()} disabled={adding || !addName.trim()}>
                  {adding ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
                  {adding ? "Starting browser…" : "Open Facebook login"}
                </Button>
              </div>
            </DialogSection>
          </div>
        ) : (
          <div className="space-y-4">
            <DialogSection>
              <div className="rounded-sm border border-border bg-bg-subtle px-3 py-2.5">
                <p className="flex items-center gap-2 text-sm font-medium">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  Waiting for you to sign in…
                </p>
                <p className="mt-1 text-xs text-ink-muted">
                  Sign in on the live page in the login tab. The session arrives automatically once you finish (up to
                  ~4 minutes). Keep this window open. If the login tab looks blank, make sure pop-ups are allowed and
                  open it again.
                </p>
              </div>
              {addError ? (
                <p className="rounded-sm border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
                  {addError}
                </p>
              ) : null}
              <div className="flex justify-end gap-2 pt-1">
                <Button type="button" variant="outline" onClick={() => void handleCancelCapture()}>
                  Cancel capture
                </Button>
                <Button type="button" onClick={openLoginTab}>
                  <ExternalLink className="h-4 w-4" aria-hidden="true" />
                  Open login tab
                </Button>
              </div>
            </DialogSection>
          </div>
        )}
      </Dialog>
    </div>
  );
}