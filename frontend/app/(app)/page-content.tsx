"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";
import { HomeView } from "@/components/views/HomeView";
import { DashboardView } from "@/components/views/DashboardView";
import { useAuth } from "@/lib/auth-context";

const HOW_IT_WORKS: ReadonlyArray<{ title: string; body: string }> = [
  {
    title: "Targets",
    body: "Paste public Facebook page or profile URLs — one per line. Each line is validated and normalized before the job starts.",
  },
  {
    title: "Filters",
    body: "Constrain the run with date ranges, post types, an optional post cap, or extra browser scroll rounds.",
  },
  {
    title: "Engines",
    body: "HTTP-only snapshots are fast and low-footprint; Browser mode goes through the page's own GraphQL feed for the full archive.",
  },
  {
    title: "Export",
    body: "Every extraction is written as JSON or CSV with honest counts — what you see in the summary matches the files on disk.",
  },
];

function HowItWorks() {
  return (
    <section className="mx-auto w-full max-w-5xl px-8" aria-label="How it works">
      <div className="pb-8 pt-10">
        <p className="text-xs font-medium text-ink-muted">How it works</p>
        <ol className="mt-6 grid grid-cols-1 gap-8 sm:grid-cols-2 lg:grid-cols-4">
          {HOW_IT_WORKS.map((step, index) => (
            <li key={step.title}>
              <p className="text-xs font-medium text-ink-muted">
                Step {index + 1}: {step.title}
              </p>
              <p className="mt-2 text-sm leading-relaxed text-ink-muted">{step.body}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

export default function HomePage() {
  const router = useRouter();
  const { user } = useAuth();

  const handleTrace = useCallback(
    (url: string) => {
      // Stash the target so a sign-in wall doesn't discard it: after auth
      // the sign-in screen redirects straight back to this investigation.
      try {
        window.sessionStorage.setItem("postharvest.pending-target", url);
      } catch {
        // Storage unavailable — the run still starts for signed-in users.
      }
      router.push(`/investigation?url=${encodeURIComponent(url)}`);
    },
    [router],
  );

  const handleAdvanced = useCallback(() => {
    router.push("/investigation");
  }, [router]);

  // Authenticated users get the operations dashboard; signed-out
  // visitors get the marketing hero. The shell already gates the rest.
  if (user) {
    return (
      <div className="w-full">
        <DashboardView />
      </div>
    );
  }

  return (
    <div className="w-full">
      <HomeView onTrace={handleTrace} onAdvanced={handleAdvanced} />
      <HowItWorks />
    </div>
  );
}