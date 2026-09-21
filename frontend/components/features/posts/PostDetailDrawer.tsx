"use client";

import { useEffect, type ReactNode } from "react";
import {
  AtSign,
  Calendar,
  Clock,
  ExternalLink,
  Hash,
  Image as ImageIcon,
  MessageCircle,
  Play,
  Share2,
  ThumbsUp,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { safeHttpUrl } from "@/lib/api";
import type { Post } from "@/lib/types";
import { cn, formatDateTime, formatNumber } from "@/lib/utils";

export interface PostDetailDrawerProps {
  post: Post | null;
  open: boolean;
  onClose: () => void;
}

const REACTIONS: Array<{ key: keyof Post; emoji: string; label: string }> = [
  { key: "reaction_like_count", emoji: "👍", label: "Like" },
  { key: "reaction_love_count", emoji: "❤️", label: "Love" },
  { key: "reaction_care_count", emoji: "🥰", label: "Care" },
  { key: "reaction_haha_count", emoji: "😂", label: "Haha" },
  { key: "reaction_wow_count", emoji: "😮", label: "Wow" },
  { key: "reaction_sad_count", emoji: "😢", label: "Sad" },
  { key: "reaction_angry_count", emoji: "😡", label: "Angry" },
];

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="border-t pt-4">
      <h4 className="mb-2 text-xs font-medium text-ink-muted">{label}</h4>
      {children}
    </section>
  );
}

function MetaItem({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] text-ink-muted">{label}</p>
      <p className="wrap-break-word font-mono text-xs">{value ?? "–"}</p>
    </div>
  );
}

export function PostDetailDrawer({ post, open, onClose }: PostDetailDrawerProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open || !post) return null;

  const postLink = safeHttpUrl(post.post_url ?? post.facebook_url);
  const profileLink = safeHttpUrl(post.profile_url);
  const thumbnail = safeHttpUrl(post.thumbnail_url);
  const mediaUrl = safeHttpUrl(post.media_url);
  const videoUrl = safeHttpUrl(post.video_url);
  const externalLinks = post.external_links.map(safeHttpUrl).filter((link): link is string => link !== null);
  const reactions = REACTIONS.map((reaction) => ({
    ...reaction,
    count: Number(post[reaction.key] ?? 0),
  })).filter((reaction) => reaction.count > 0);
  const maxReaction = reactions.reduce((max, reaction) => Math.max(max, reaction.count), 0);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Post details">
      <div className="absolute inset-0 bg-black/50 animate-fade-in" onClick={onClose} aria-hidden="true" />
      <aside className="relative z-10 flex h-full w-full max-w-xl animate-slide-in-right flex-col border-l bg-bg">
        {/* Header */}
        <header className="flex items-start justify-between gap-4 border-b px-5 py-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="truncate text-base font-semibold">{post.page_name ?? "Unknown page"}</h3>
              {post.post_type ? <Badge variant="secondary">{post.post_type}</Badge> : null}
            </div>
            <p className="mt-1 text-xs text-ink-muted">
              {profileLink ? (
                <a href={profileLink} target="_blank" rel="noopener noreferrer" className="hover:underline">
                  {post.profile_url ?? post.facebook_url ?? "Profile"}
                </a>
              ) : (
                (post.profile_url ?? post.facebook_url ?? "–")
              )}
            </p>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close post details">
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {/* Meta */}
          <Section label="Details">
            <div className="grid grid-cols-2 gap-3">
              <div className="flex items-center gap-2 text-sm text-ink-muted">
                <Calendar className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span>{formatDateTime(post.published_at ?? post.timestamp)}</span>
              </div>
              <div className="flex items-center gap-2 text-sm text-ink-muted">
                <Clock className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span>ts {post.timestamp != null ? post.timestamp : "–"}</span>
              </div>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <MetaItem label="Post ID" value={post.post_id} />
              <MetaItem label="Page ID" value={post.page_id} />
            </div>
          </Section>

          {/* Text */}
          {post.text ? (
            <Section label="Post text">
              <p className="whitespace-pre-wrap wrap-break-word text-sm leading-relaxed">{post.text}</p>
            </Section>
          ) : null}
          {post.caption ? (
            <Section label="Caption">
              <p className="whitespace-pre-wrap wrap-break-word text-sm leading-relaxed text-ink-muted">{post.caption}</p>
            </Section>
          ) : null}

          {/* Tags / mentions / external links */}
          {post.hashtags.length > 0 ? (
            <Section label={`Hashtags (${post.hashtags.length})`}>
              <div className="flex flex-wrap gap-1.5">
                {post.hashtags.map((tag) => (
                  <Badge key={tag} variant="blue" className="gap-1">
                    <Hash className="h-3 w-3" aria-hidden="true" /> {tag}
                  </Badge>
                ))}
              </div>
            </Section>
          ) : null}
          {post.mentions.length > 0 ? (
            <Section label={`Mentions (${post.mentions.length})`}>
              <div className="flex flex-wrap gap-1.5">
                {post.mentions.map((mention) => (
                  <Badge key={mention} variant="purple" className="gap-1">
                    <AtSign className="h-3 w-3" aria-hidden="true" /> {mention}
                  </Badge>
                ))}
              </div>
            </Section>
          ) : null}
          {externalLinks.length > 0 ? (
            <Section label="External links">
              <ul className="space-y-1.5">
                {externalLinks.map((link) => (
                  <li key={link}>
                    <a
                      href={link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex max-w-full items-center gap-1.5 truncate text-sm text-primary hover:underline"
                    >
                      <ExternalLink className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                      <span className="truncate">{link}</span>
                    </a>
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}

          {/* Engagement */}
          <Section label="Engagement">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
              {[
                { icon: ThumbsUp, label: "Likes", value: post.likes },
                { icon: ThumbsUp, label: "Reactions", value: post.reactions },
                { icon: MessageCircle, label: "Comments", value: post.comments_count },
                { icon: Share2, label: "Shares", value: post.shares },
                { icon: Play, label: "Views", value: post.views_count },
              ].map((item) => (
                <div key={item.label} className="rounded-lg border bg-bg-subtle/30 px-3 py-2 text-center">
                  <item.icon className="mx-auto h-4 w-4 text-ink-muted" aria-hidden="true" />
                  <p className="mt-1 text-base font-semibold tabular-nums">{formatNumber(item.value)}</p>
                  <p className="text-[11px] text-ink-muted">{item.label}</p>
                </div>
              ))}
            </div>
          </Section>

          {/* Reaction breakdown */}
          {reactions.length > 0 ? (
            <Section label="Reaction breakdown">
              <ul className="space-y-2">
                {reactions.map((reaction) => (
                  <li key={reaction.key} className="flex items-center gap-3">
                    <span className="w-6 text-center text-sm" aria-hidden="true">{reaction.emoji}</span>
                    <span className="w-14 shrink-0 text-xs text-ink-muted">{reaction.label}</span>
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-bg-subtle">
                      <div
                        className="h-full rounded-full bg-primary/70"
                        style={{ width: `${maxReaction > 0 ? (reaction.count / maxReaction) * 100 : 0}%` }}
                      />
                    </div>
                    <span className="w-10 shrink-0 text-right text-xs tabular-nums text-ink-muted">
                      {formatNumber(reaction.count)}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}

          {/* Media */}
          {thumbnail || mediaUrl || videoUrl ? (
            <Section label="Media">
              <div className="space-y-2">
                {thumbnail ? (
                  <div className="overflow-hidden rounded-lg border bg-bg-subtle/30">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={thumbnail}
                      alt=""
                      loading="lazy"
                      className="max-h-72 w-full object-contain"
                    />
                  </div>
                ) : null}
                {post.media_type ? (
                  <p className="flex items-center gap-1.5 text-xs text-ink-muted">
                    <ImageIcon className="h-3.5 w-3.5" aria-hidden="true" /> {post.media_type}
                  </p>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  {videoUrl ? (
                    <a href={videoUrl} target="_blank" rel="noopener noreferrer">
                      <Button type="button" variant="secondary" size="sm">
                        <Play className="h-3.5 w-3.5" aria-hidden="true" /> Open video
                      </Button>
                    </a>
                  ) : null}
                  {mediaUrl ? (
                    <a href={mediaUrl} target="_blank" rel="noopener noreferrer">
                      <Button type="button" variant="outline" size="sm">
                        <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" /> Open media
                      </Button>
                    </a>
                  ) : null}
                </div>
              </div>
            </Section>
          ) : null}

          {/* Transcript */}
          {post.transcript ? (
            <Section label="Transcript">
              <div className="flex items-center gap-2">
                {post.transcript_language ? <Badge variant="secondary">{post.transcript_language}</Badge> : null}
              </div>
              <p className="mt-2 max-h-56 overflow-y-auto whitespace-pre-wrap wrap-break-word rounded-lg border bg-bg-subtle/30 p-3 text-sm leading-relaxed">
                {post.transcript}
              </p>
            </Section>
          ) : null}
        </div>

        {/* Footer */}
        <footer className="border-t px-5 py-3">
          {postLink ? (
            <a href={postLink} target="_blank" rel="noopener noreferrer" className={cn("w-full")}>
              <Button type="button" className="w-full">
                <ExternalLink className="h-4 w-4" aria-hidden="true" /> Open post on Facebook
              </Button>
            </a>
          ) : null}
        </footer>
      </aside>
    </div>
  );
}