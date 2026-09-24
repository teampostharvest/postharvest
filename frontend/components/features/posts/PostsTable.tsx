"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ExternalLink,
  Inbox,
  Play,
  Search,
  X,
} from "lucide-react";
import { ApiErrorBanner } from "@/components/features/scraper/ApiErrorBanner";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { safeHttpUrl } from "@/lib/api";
import type { Post, PostType } from "@/lib/types";
import { cn, formatDate, formatNumber, postSortDate } from "@/lib/utils";

export interface PostsTableProps {
  posts: Post[];
  total: number;
  loading: boolean;
  loaded: boolean;
  error: string | null;
  onRetry: () => void;
  onSelectPost: (post: Post) => void;
}

type SortKey = "date" | "likes" | "comments" | "shares";
type SortDirection = "asc" | "desc";

const NUMERIC_ACCESSORS: Record<Exclude<SortKey, "date">, keyof Pick<Post, "likes" | "comments_count" | "shares">> = {
  likes: "likes",
  comments: "comments_count",
  shares: "shares",
};

const TYPE_BADGE: Record<string, BadgeVariant> = {
  text: "secondary",
  image: "secondary",
  video: "secondary",
  link: "secondary",
};

const PAGE_SIZES = [10, 20, 50, 100];

function typeVariant(postType: PostType | null): BadgeVariant {
  return (postType && TYPE_BADGE[postType]) || "outline";
}

function MediaThumb({ post }: { post: Post }) {
  const [failed, setFailed] = useState(false);
  const src = safeHttpUrl(post.thumbnail_url);
  if (src && !failed) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={src}
        alt=""
        loading="lazy"
        className="h-10 w-10 shrink-0 rounded-md border object-cover"
        onError={() => setFailed(true)}
      />
    );
  }
  const isVideo = post.post_type === "video" || (post.media_type ?? "").toLowerCase() === "video";
  if (isVideo && safeHttpUrl(post.video_url)) {
    return (
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border bg-bg-subtle text-ink-muted">
        <Play className="h-4 w-4" aria-hidden="true" />
      </span>
    );
  }
  return <span className="w-10 shrink-0 text-center text-ink-muted" aria-label="No media">–</span>;
}

interface SortHeaderProps {
  label: string;
  active: boolean;
  direction: SortDirection;
  onClick: () => void;
}

function SortHeader({ label, active, direction, onClick }: SortHeaderProps) {
  return (
    <th scope="col" className="px-3 py-3 text-left text-xs font-medium text-ink-muted">
      <button
        type="button"
        onClick={onClick}
        className={cn("inline-flex items-center gap-1 rounded transition-colors hover:text-ink", active && "text-ink")}
      >
        {label}
        {active ? (
          direction === "asc" ? (
            <ArrowUp className="h-3 w-3" aria-hidden="true" />
          ) : (
            <ArrowDown className="h-3 w-3" aria-hidden="true" />
          )
        ) : (
          <ArrowUpDown className="h-3 w-3 opacity-40" aria-hidden="true" />
        )}
      </button>
    </th>
  );
}

export function PostsTable({ posts, total, loading, loaded, error, onRetry, onSelectPost }: PostsTableProps) {
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("date");
  const [sortDir, setSortDir] = useState<SortDirection>("desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  useEffect(() => {
    setPage(1);
  }, [query, sortKey, sortDir, pageSize]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((current) => (current === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "date" ? "desc" : "desc");
    }
  };

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return posts;
    return posts.filter((post) => {
      const haystack = [
        post.page_name,
        post.page_id,
        post.post_id,
        post.text,
        post.caption,
        ...post.hashtags,
        ...post.mentions,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [posts, query]);

  const sorted = useMemo(() => {
    const direction = sortDir === "asc" ? 1 : -1;
    const sortedPosts = [...filtered];
    sortedPosts.sort((a, b) => {
      let comparison: number;
      if (sortKey === "date") {
        comparison = postSortDate(a) - postSortDate(b);
      } else {
        const accessor = NUMERIC_ACCESSORS[sortKey];
        comparison = (a[accessor] ?? -1) - (b[accessor] ?? -1);
      }
      return comparison * direction;
    });
    return sortedPosts;
  }, [filtered, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const pageItems = sorted.slice((safePage - 1) * pageSize, safePage * pageSize);
  const rangeStart = sorted.length === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const rangeEnd = Math.min(safePage * pageSize, sorted.length);

  return (
    <Card>
      <CardHeader className="space-y-3 pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle>Posts preview</CardTitle>
          <div className="flex items-center gap-2 text-sm text-ink-muted">
            <span className="tabular-nums">{formatNumber(total)}</span> post{total === 1 ? "" : "s"} in dataset
          </div>
        </div>
        <div className="relative w-full sm:max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-muted" aria-hidden="true" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search page, text, hashtags…"
            className="pl-9 pr-9"
            aria-label="Search posts"
          />
          {query ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2"
              onClick={() => setQuery("")}
              aria-label="Clear search"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {error ? (
          <ApiErrorBanner title="Could not load posts" message={error} onRetry={onRetry} />
        ) : loading && posts.length === 0 ? (
          <div className="space-y-2" aria-label="Loading posts">
            {Array.from({ length: 6 }).map((_, index) => (
              <div key={index} className="flex animate-pulse items-center gap-4 rounded-lg border bg-bg-subtle/20 px-3 py-3">
                <div className="h-10 w-10 rounded bg-ink-muted/15" />
                <div className="flex-1 space-y-2">
                  <div className="h-3 w-2/5 rounded bg-ink-muted/15" />
                  <div className="h-3 w-3/5 rounded bg-ink-muted/10" />
                </div>
                <div className="h-4 w-16 rounded bg-ink-muted/10" />
              </div>
            ))}
          </div>
        ) : pageItems.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-6 py-14 text-center">
            <Inbox className="h-8 w-8 text-ink-muted/60" aria-hidden="true" />
            <p className="text-sm font-medium">{loaded ? (query ? "No posts match your search." : "No posts were extracted.") : "Loading…"}</p>
            <p className="max-w-sm text-xs text-ink-muted">
              {query
                ? "Try a different search term, or export the full dataset."
                : "The job completed without posts. Check the job status above or run a new scrape with different URLs."}
            </p>
          </div>
        ) : (
          <div className="-mx-6 overflow-x-auto px-6">
            <table className="w-full min-w-[860px] border-collapse text-sm">
              <thead>
                <tr className="border-b text-left">
                  <SortHeader label="Date" active={sortKey === "date"} direction={sortDir} onClick={() => toggleSort("date")} />
                  <th scope="col" className="px-3 py-3 text-left text-xs font-medium text-ink-muted">Page</th>
                  <th scope="col" className="px-3 py-3 text-left text-xs font-medium text-ink-muted">Post text</th>
                  <th scope="col" className="px-3 py-3 text-left text-xs font-medium text-ink-muted">Type</th>
                  <SortHeader label="Likes" active={sortKey === "likes"} direction={sortDir} onClick={() => toggleSort("likes")} />
                  <SortHeader label="Comments" active={sortKey === "comments"} direction={sortDir} onClick={() => toggleSort("comments")} />
                  <SortHeader label="Shares" active={sortKey === "shares"} direction={sortDir} onClick={() => toggleSort("shares")} />
                  <th scope="col" className="px-3 py-3 text-left text-xs font-medium text-ink-muted">Media</th>
                  <th scope="col" className="px-3 py-3 text-left text-xs font-medium text-ink-muted">Post URL</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {pageItems.map((post, index) => {
                  const postLink = safeHttpUrl(post.post_url ?? post.facebook_url);
                  return (
                    <tr
                      key={post.post_id ?? `${post.post_url ?? "post"}-${index}`}
                      className="cursor-pointer transition-colors hover:bg-bg-subtle/40 focus-visible:bg-bg-subtle/40"
                      tabIndex={0}
                      role="button"
                      aria-label={`Open post details${post.page_name ? ` from ${post.page_name}` : ""}`}
                      onClick={() => onSelectPost(post)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onSelectPost(post);
                        }
                      }}
                    >
                      <td className="whitespace-nowrap px-3 py-3 tabular-nums text-ink-muted">
                        <span title={post.published_at ?? undefined}>{formatDate(post.published_at ?? post.timestamp)}</span>
                      </td>
                      <td className="max-w-[160px] px-3 py-3">
                        <p className="truncate font-medium" title={post.page_name ?? undefined}>{post.page_name ?? "–"}</p>
                        {post.page_id ? <p className="truncate text-xs text-ink-muted">ID {post.page_id}</p> : null}
                      </td>
                      <td className="max-w-[300px] px-3 py-3">
                        <p className="line-clamp-2 wrap-break-word text-ink-muted">
                          {post.text ?? post.caption ?? <span className="italic">No text</span>}
                        </p>
                      </td>
                      <td className="whitespace-nowrap px-3 py-3">
                        <Badge variant={typeVariant(post.post_type)}>{post.post_type ?? "unknown"}</Badge>
                      </td>
                      <td className="whitespace-nowrap px-3 py-3 tabular-nums">{formatNumber(post.likes)}</td>
                      <td className="whitespace-nowrap px-3 py-3 tabular-nums">{formatNumber(post.comments_count)}</td>
                      <td className="whitespace-nowrap px-3 py-3 tabular-nums">{formatNumber(post.shares)}</td>
                      <td className="px-3 py-3">
                        <MediaThumb post={post} />
                      </td>
                      <td className="whitespace-nowrap px-3 py-3">
                        {postLink ? (
                          <a
                            href={postLink}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label={`Open post on Facebook${post.page_name ? ` (${post.page_name})` : ""}`}
                            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-accent hover:text-ink"
                            onClick={(event) => event.stopPropagation()}
                          >
                            <ExternalLink className="h-4 w-4" aria-hidden="true" />
                          </a>
                        ) : (
                          <span className="text-ink-muted">–</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {!error && pageItems.length > 0 ? (
          <div className="flex flex-col-reverse items-center justify-between gap-3 sm:flex-row">
            <p className="text-xs tabular-nums text-ink-muted">
              Showing <span className="font-medium text-ink">{rangeStart}–{rangeEnd}</span> of{" "}
              <span className="font-medium text-ink">{formatNumber(sorted.length)}</span> filtered post{sorted.length === 1 ? "" : "s"}
            </p>
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-ink-muted">Rows</span>
                <Select
                  value={String(pageSize)}
                  onChange={(event) => setPageSize(Number(event.target.value))}
                  className="h-8 w-[72px] text-xs"
                  aria-label="Rows per page"
                >
                  {PAGE_SIZES.map((size) => (
                    <option key={size} value={String(size)}>{size}</option>
                  ))}
                </Select>
              </div>
              <Button variant="outline" size="sm" disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>
                Previous
              </Button>
              <span className="min-w-[70px] text-center text-xs tabular-nums text-ink-muted">
                Page {safePage} / {pageCount}
              </span>
              <Button variant="outline" size="sm" disabled={safePage >= pageCount} onClick={() => setPage(safePage + 1)}>
                Next
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}