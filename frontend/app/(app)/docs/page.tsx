import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Eyebrow, PageHeading } from "@/components/views/Display";
import { DOCS_SECTIONS, findDocsPage } from "@/lib/docs-meta";
import { getDocsBody } from "./content";

export const metadata: Metadata = {
  title: "Documentation",
  description:
    "Setup, usage guides and API reference for the PostHarvest. Public data, throttled, never behind auth.",
};

export default function DocsOverviewPage() {
  const overview = findDocsPage("overview");
  return (
    <>
      <header className="border-b border-border pb-8">
        <Eyebrow>Documentation</Eyebrow>
        <PageHeading>Docs</PageHeading>
        <p className="max-w-2xl text-sm leading-relaxed text-ink-muted">
          {overview?.description ??
            "How to scrape public Facebook posts ethically, throttle safely, and use every endpoint."}
        </p>
      </header>

      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Documentation sections">
        {DOCS_SECTIONS.map((section) => (
          <div key={section.title} className="rounded-md border border-border bg-bg-elevated shadow-sm">
            <h2 className="border-b border-border px-4 py-2.5 text-xs font-medium text-ink-muted">
              {section.title}
            </h2>
            <ul className="divide-y divide-border/60">
              {section.pages.map((page) => (
                <li key={page.slug}>
                  <Link
                    href={page.slug === "overview" ? "/docs" : `/docs/${page.slug}`}
                    className="group flex items-center justify-between gap-2 px-4 py-2.5 text-sm transition-colors hover:bg-bg-subtle"
                  >
                    <span className="truncate">{page.title}</span>
                    <ArrowRight
                      className="h-3.5 w-3.5 shrink-0 text-ink-muted transition-transform group-hover:translate-x-0.5"
                      strokeWidth={1.75}
                      aria-hidden="true"
                    />
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>

      <section className="space-y-4">{getDocsBody("overview")}</section>
    </>
  );
}