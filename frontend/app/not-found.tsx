import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft, BookOpen } from "lucide-react";

export const metadata: Metadata = {
  title: "404 · Page not found",
  description: "This page could not be found.",
};

export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-bg px-6 py-24 text-ink">
      <p className="text-xs font-medium text-ink-muted" aria-hidden="true">
        Error 404
      </p>

      <h1 className="mt-4 font-display text-7xl font-semibold tracking-tight sm:text-8xl">
        4<span className="text-accent">0</span>4
      </h1>

      <p className="mt-4 max-w-md text-center text-sm leading-relaxed text-ink-muted">
        This page drifted off the timeline. The post you&apos;re looking for either never existed or was
        scrubbed — check the URL, or head back to the dashboard.
      </p>

      <div className="mt-10 h-px w-24 bg-accent/40" aria-hidden="true" />

      <nav className="mt-10 flex items-center gap-6">
        <Link
          href="/"
          className="group inline-flex items-center gap-2 rounded-sm border border-border-strong bg-bg-elevated px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-bg-subtle"
        >
          <ArrowLeft className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          Back to dashboard
        </Link>
        <Link
          href="/docs"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-highlight transition-colors hover:text-ink"
        >
          <BookOpen className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          Read the docs
        </Link>
      </nav>

      <p className="mt-16 text-xs text-ink-faint">Facebook posts scraper — 404 boundary</p>
    </main>
  );
}