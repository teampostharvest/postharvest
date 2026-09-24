import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PostDetailDrawer } from "@/components/features/posts/PostDetailDrawer";
import type { Post } from "@/lib/types";

function makePost(overrides: Partial<Post> = {}): Post {
  return {
    post_id: "p-1",
    facebook_url: null,
    post_url: "https://www.facebook.com/123/posts/456",
    page_name: "Alpha News",
    page_id: "123",
    profile_url: null,
    post_type: "video",
    published_at: "2026-09-01T12:00:00Z",
    timestamp: null,
    text: "The full post body text.",
    caption: null,
    hashtags: ["#tech", "#facebook"],
    mentions: ["@journalist"],
    external_links: ["https://example.com/article"],
    likes: 120,
    reactions: 140,
    comments_count: 8,
    shares: 3,
    views_count: 5000,
    reaction_like_count: 100,
    reaction_love_count: 40,
    reaction_care_count: 0,
    reaction_haha_count: 0,
    reaction_wow_count: 0,
    reaction_sad_count: 0,
    reaction_angry_count: 0,
    media_type: "video/mp4",
    thumbnail_url: "https://example.com/thumb.jpg",
    media_url: null,
    video_url: "https://example.com/video.mp4",
    transcript: "spoken word here",
    transcript_language: "en",
    ...overrides,
  };
}

describe("PostDetailDrawer", () => {
  it("renders nothing when closed", () => {
    const { container } = render(<PostDetailDrawer post={makePost()} open={false} onClose={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing without a post", () => {
    const { container } = render(<PostDetailDrawer post={null} open onClose={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the page header, meta, text and engagement sections", () => {
    render(<PostDetailDrawer post={makePost()} open onClose={vi.fn()} />);

    expect(screen.getByRole("dialog", { name: "Post details" })).toBeInTheDocument();
    expect(screen.getByText("Alpha News")).toBeInTheDocument();
    expect(screen.getByText("video")).toBeInTheDocument();
    expect(screen.getByText("Post ID")).toBeInTheDocument();
    expect(screen.getByText("p-1")).toBeInTheDocument();
    expect(screen.getByText("Page ID")).toBeInTheDocument();
    expect(screen.getByText("123")).toBeInTheDocument();
    expect(screen.getByText("The full post body text.")).toBeInTheDocument();

    // Engagement grid
    expect(screen.getByText("120")).toBeInTheDocument(); // likes
    expect(screen.getByText("5,000")).toBeInTheDocument(); // views

    // Hashtags, mentions, external links
    expect(screen.getByText("#tech")).toBeInTheDocument();
    expect(screen.getByText("@journalist")).toBeInTheDocument();
    expect(screen.getByText("https://example.com/article")).toBeInTheDocument();

    // Reaction breakdown (only non-zero reaction rows render)
    expect(screen.getByText("Reaction breakdown")).toBeInTheDocument();
    expect(screen.getByText("Like")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();

    // Transcript
    expect(screen.getByText("Transcript")).toBeInTheDocument();
    expect(screen.getByText("spoken word here")).toBeInTheDocument();
  });

  it("renders media actions and the footer CTA", () => {
    render(<PostDetailDrawer post={makePost()} open onClose={vi.fn()} />);

    const thumb = screen.getByAltText("") as HTMLImageElement;
    expect(thumb.src).toContain("example.com/thumb.jpg");

    const videoLink = screen.getByRole("link", { name: /open video/i });
    expect(videoLink).toHaveAttribute("href", "https://example.com/video.mp4");

    const footer = screen.getByRole("link", { name: /open post on facebook/i });
    expect(footer).toHaveAttribute("href", "https://www.facebook.com/123/posts/456");
  });

  it("closes via the close button", () => {
    const onClose = vi.fn();
    render(<PostDetailDrawer post={makePost()} open onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: /close post details/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape and on backdrop click", () => {
    const onClose = vi.fn();
    const { container } = render(<PostDetailDrawer post={makePost()} open onClose={onClose} />);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);

    const backdrop = container.querySelector('[aria-hidden="true"]');
    expect(backdrop).not.toBeNull();
    fireEvent.click(backdrop as Element);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("omits absent sections instead of fabricating them", () => {
    const post = makePost({
      text: null,
      caption: "only a caption",
      hashtags: [],
      mentions: [],
      external_links: [],
      reaction_like_count: 0,
      reaction_love_count: 0,
      transcript: null,
      thumbnail_url: null,
      video_url: null,
      media_url: "https://example.com/media.png",
    });
    render(<PostDetailDrawer post={post} open onClose={vi.fn()} />);

    expect(screen.queryByText("Post text")).not.toBeInTheDocument();
    expect(screen.getByText("only a caption")).toBeInTheDocument();
    expect(screen.queryByText("Transcript")).not.toBeInTheDocument();
    expect(screen.queryByText("Reaction breakdown")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open media/i })).toHaveAttribute(
      "href",
      "https://example.com/media.png"
    );
  });
});