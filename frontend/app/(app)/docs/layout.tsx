"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { DOCS_PAGES, findDocsPage } from "@/lib/docs-meta";
import { cn } from "@/lib/utils";

const SOURCE_FILES: ReadonlyArray<{ path: string; description: string }> = [
  { path: "cli.py", description: "Login, scrape and export from the shell" },
  { path: "backend/main.py", description: "FastAPI app entry point" },
  { path: "backend/core/job_manager.py", description: "run lifecycle, ledger + progress TTL" },
  { path: "backend/api/scrape.py", description: "start / stop / inspect endpoints" },
  { path: "backend/api/jobs.py", description: "run list + status endpoints" },
  { path: "backend/scraper/crawler.py", description: "capture pipeline orchestration" },
  { path: "backend/scraper/fetcher.py", description: "throttled HTTP fetching" },
  { path: "backend/scraper/browser_scraper.py", description: "Playwright GraphQL feed mode" },
  { path: "backend/scraper/parser.py", description: "posts from snapshot / feed JSON" },
  { path: "backend/scraper/normalizer.py", description: "canonical per-post shape" },
  { path: "backend/exporters/json_exporter.py", description: "JSON + JSONL output" },
  { path: "backend/exporters/csv_exporter.py", description: "CSV output" },
];

function DocsFileRail() {
  return (
    <aside className="hidden lg:block">
      <div className="sticky top-0 border border-border bg-bg">
        <p className="border-b border-border px-4 py-2.5 text-xs font-medium text-ink-muted">
          Sources
        </p>
        <ul className="divide-y divide-border/60">
          {SOURCE_FILES.map((file) => (
            <li key={file.path} className="px-4 py-2.5">
              <p className="truncate font-mono text-xs text-ink">{file.path}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">{file.description}</p>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}

/**
 * Docs shell: breadcrumb on top, the page body from each route below, and a
 * prev/next footer that walks the canonical documentation order. Two-column —
 * wide left narrative, sticky "Sources" file rail on the right (MailAccess).
 */
export default function DocsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const slug = pathname === "/docs" ? "overview" : pathname.replace(/^\/docs\/?/, "");
  const page = findDocsPage(slug);
  const index = page ? DOCS_PAGES.findIndex((entry) => entry.slug === page.slug) : -1;
  const prev = index > 0 ? DOCS_PAGES[index - 1] : null;
  const next = index >= 0 && index < DOCS_PAGES.length - 1 ? DOCS_PAGES[index + 1] : null;

  return (
    <div>
      <nav className="mb-6 flex items-center gap-2 text-xs font-medium text-ink-muted" aria-label="Breadcrumb">
        <Link href="/docs" className="transition-colors hover:text-ink">
          Docs
        </Link>
        <span aria-hidden="true">/</span>
        <span className="truncate text-ink">{page?.title ?? "Documentation"}</span>
      </nav>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[1fr_272px]">
        <div className="min-w-0 space-y-6">{children}</div>
        <DocsFileRail />
      </div>

      {prev || next ? (
        <nav
          className="mt-10 grid grid-cols-1 gap-3 border-t border-border pt-5 sm:grid-cols-2"
          aria-label="Page navigation"
        >
          {prev ? (
            <Link
              href={`/docs/${prev.slug}`}
              className="group border border-border px-3 py-3 transition-colors hover:border-ink"
            >
              <span className="flex items-center gap-1 text-xs font-medium text-ink-muted">
                <ChevronLeft className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" /> Previous
              </span>
              <span className="mt-1.5 block truncate text-sm font-medium text-ink">{prev.title}</span>
            </Link>
          ) : (
            <span />
          )}
          {next ? (
            <Link
              href={`/docs/${next.slug}`}
              className={cn(
                "group border border-border px-3 py-3 text-right transition-colors hover:border-ink",
                !prev && "sm:col-start-2",
              )}
            >
              <span className="flex items-center justify-end gap-1 text-xs font-medium text-ink-muted">
                Next <ChevronRight className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
              </span>
              <span className="mt-1.5 block truncate text-sm font-medium text-ink">{next.title}</span>
            </Link>
          ) : (
            <span />
          )}
        </nav>
      ) : null}
    </div>
  );
}