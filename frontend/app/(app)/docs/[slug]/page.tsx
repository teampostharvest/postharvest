import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Eyebrow, PageHeading } from "@/components/views/Display";
import { DOCS_PAGES, findDocsPage } from "@/lib/docs-meta";
import { getDocsBody } from "../content";

export const dynamic = "force-static";

export function generateStaticParams(): Array<{ slug: string }> {
  return DOCS_PAGES.filter((page) => page.slug !== "overview").map((page) => ({ slug: page.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const page = findDocsPage(slug);
  return {
    title: page ? `${page.title} · Documentation` : "Documentation",
    description: page?.description,
  };
}

export default async function DocsSlugPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const page = findDocsPage(slug);
  if (!page) notFound();
  const body = getDocsBody(slug);

  return (
    <>
      <header className="border-b border-border pb-8">
        <Eyebrow>Documentation</Eyebrow>
        <PageHeading>{page.title}</PageHeading>
        <p className="max-w-2xl text-sm leading-relaxed text-ink-muted">{page.description}</p>
      </header>
      <div className="space-y-4">{body}</div>
    </>
  );
}