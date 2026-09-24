import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KpiCards } from "@/components/features/metrics/KpiCards";
import type { Post } from "@/lib/types";

function mockPost(overrides: Partial<Post> = {}): Post {
  return {
    post_id: "123_456",
    post_url: "https://facebook.com/123/posts/456",
    facebook_url: null,
    page_name: "Test Page",
    page_id: "123",
    profile_url: null,
    post_type: "text",
    published_at: "2026-09-17T12:00:00Z",
    timestamp: 1789646400,
    text: "Sample post content",
    caption: null,
    hashtags: [],
    mentions: [],
    external_links: [],
    likes: 10,
    reactions: 10,
    comments_count: 5,
    shares: 2,
    views_count: null,
    reaction_like_count: null,
    reaction_love_count: null,
    reaction_care_count: null,
    reaction_haha_count: null,
    reaction_wow_count: null,
    reaction_sad_count: null,
    reaction_angry_count: null,
    media_type: null,
    thumbnail_url: null,
    media_url: null,
    video_url: null,
    transcript: null,
    transcript_language: null,
    ...overrides,
  };
}

describe("KpiCards", () => {
  it("calculates aggregate metrics correctly from post array", () => {
    const posts = [
      mockPost({ likes: 100, comments_count: 20, shares: 10, post_type: "video" }),
      mockPost({ likes: 50, comments_count: 10, shares: 5, post_type: "image" }),
      mockPost({ likes: 0, comments_count: 0, shares: 0, external_links: ["https://example.com"] }),
    ];

    render(
      <KpiCards
        posts={posts}
        total={3}
        capped={false}
        loading={false}
        error={null}
        onRetry={vi.fn()}
      />
    );

    // Total likes: 150
    expect(screen.getByText("150")).toBeInTheDocument();
    // Total comments: 30
    expect(screen.getByText("30")).toBeInTheDocument();
    // Total shares: 15
    expect(screen.getByText("15")).toBeInTheDocument();
  });

  it("renders error banner when error prop is present", () => {
    const onRetry = vi.fn();
    render(
      <KpiCards
        posts={[]}
        total={0}
        capped={false}
        loading={false}
        error="Unable to fetch KPI metrics"
        onRetry={onRetry}
      />
    );

    expect(screen.getByText("Could not load results")).toBeInTheDocument();
    expect(screen.getByText("Unable to fetch KPI metrics")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
