"use client";

import { useMemo } from "react";
import {
  FileText,
  Image as ImageIcon,
  Link2,
  MessageCircle,
  Share2,
  ThumbsUp,
  Video,
} from "lucide-react";
import { ApiErrorBanner } from "@/components/features/scraper/ApiErrorBanner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Post } from "@/lib/types";
import { formatCompact, formatNumber, pluralize } from "@/lib/utils";

export interface KpiCardsProps {
  posts: Post[];
  /** Server-reported total post count (may exceed loaded posts when capped). */
  total: number;
  /** True when the dataset exceeds the load cap, so aggregates are partial. */
  capped: boolean;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

interface Kpi {
  label: string;
  value: string;
  caption: string;
  icon: typeof ThumbsUp;
}

const ICON_TONES = "bg-bg-subtle text-ink-muted";

export function KpiCards({ posts, total, capped, loading, error, onRetry }: KpiCardsProps) {
  const aggregates = useMemo(() => {
    let likes = 0;
    let comments = 0;
    let shares = 0;
    let videos = 0;
    let images = 0;
    let links = 0;
    for (const post of posts) {
      likes += post.likes ?? 0;
      comments += post.comments_count ?? 0;
      shares += post.shares ?? 0;
      const mediaType = (post.media_type ?? "").toLowerCase();
      if (mediaType === "video" || post.post_type === "video") videos += 1;
      if (mediaType === "image" || post.post_type === "image") images += 1;
      if (post.post_type === "link" || (post.external_links?.length ?? 0) > 0) links += 1;
    }
    return { likes, comments, shares, videos, images, links };
  }, [posts]);

  const kpis: Kpi[] = useMemo(() => {
    const loadedCaption = capped
      ? `first ${formatNumber(posts.length)} loaded of ${formatNumber(total)}`
      : posts.length === 1
        ? "1 post loaded"
        : `${formatNumber(posts.length)} posts loaded`;
    return [
      { label: "Total Posts", value: formatNumber(total || posts.length), caption: capped ? `dataset: ${formatNumber(total)}` : loadedCaption, icon: FileText },
      { label: "Total Likes", value: formatCompact(aggregates.likes), caption: pluralize(aggregates.likes, "like"), icon: ThumbsUp },
      { label: "Total Comments", value: formatCompact(aggregates.comments), caption: pluralize(aggregates.comments, "comment"), icon: MessageCircle },
      { label: "Total Shares", value: formatCompact(aggregates.shares), caption: pluralize(aggregates.shares, "share"), icon: Share2 },
      { label: "Videos", value: formatNumber(aggregates.videos), caption: "video / reel posts", icon: Video },
      { label: "Images", value: formatNumber(aggregates.images), caption: "photo posts", icon: ImageIcon },
      { label: "Links", value: formatNumber(aggregates.links), caption: "link posts", icon: Link2 },
    ];
  }, [aggregates, capped, posts.length, total]);

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-4">
        <CardTitle>Results overview</CardTitle>
        {capped ? (
          <p className="rounded-md border border-danger/30 bg-danger/10 px-3 py-1 text-xs text-danger">
            Aggregates shown for the first {formatNumber(posts.length)} posts, export for the full dataset
          </p>
        ) : null}
      </CardHeader>
      <CardContent>
        {error ? (
          <ApiErrorBanner title="Could not load results" message={error} onRetry={onRetry} />
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
            {loading
              ? Array.from({ length: 7 }).map((_, index) => (
                  <div key={index} className="rounded-xl border bg-card p-4">
                    <Skeleton className="h-8 w-8 rounded-lg" />
                    <Skeleton className="mt-3 h-6 w-14" />
                    <Skeleton className="mt-2 h-3 w-20" />
                  </div>
                ))
              : kpis.map((kpi) => (
                  <div key={kpi.label} className="rounded-lg border bg-card p-4 transition-colors hover:bg-bg-subtle/60">
                    <div className={`inline-flex h-8 w-8 items-center justify-center rounded-md ${ICON_TONES}`}>
                      <kpi.icon className="h-4 w-4" aria-hidden="true" />
                    </div>
                    <p className="mt-3 truncate text-2xl font-light tracking-tighter tabular-nums leading-7 text-ink">{kpi.value}</p>
                    <p className="mt-1 text-xs font-medium text-ink-muted">
                      {kpi.label}
                    </p>
                    <p className="truncate text-xs text-ink-muted" title={kpi.caption}>
                      {kpi.caption}
                    </p>
                  </div>
                ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}