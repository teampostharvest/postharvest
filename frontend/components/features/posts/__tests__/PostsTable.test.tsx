import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PostsTable } from "@/components/features/posts/PostsTable";
import type { Post } from "@/lib/types";

function makePost(overrides: Partial<Post> = {}): Post {
  return {
    post_id: null,
    facebook_url: null,
    post_url: null,
    page_name: "Test Page",
    page_id: "123",
    profile_url: null,
    post_type: "text",
    published_at: "2026-09-01T12:00:00Z",
    timestamp: null,
    text: "Sample post",
    caption: null,
    hashtags: [],
    mentions: [],
    external_links: [],
    likes: 0,
    reactions: null,
    comments_count: 0,
    shares: 0,
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

function renderTable(props: Partial<Parameters<typeof PostsTable>[0]> = {}) {
  const defaults = {
    posts: [],
    total: 0,
    loading: false,
    loaded: false,
    error: null as string | null,
    onRetry: vi.fn(),
    onSelectPost: vi.fn(),
  };
  const merged = { ...defaults, ...props };
  return {
    render: () => render(<PostsTable {...merged} />),
    onRetry: merged.onRetry,
    onSelectPost: merged.onSelectPost,
  };
}

describe("PostsTable", () => {
  it("shows the dataset count and renders post rows", () => {
    const posts = [
      makePost({ post_id: "p1", page_name: "Alpha News", likes: 10, comments_count: 2, shares: 1 }),
      makePost({ post_id: "p2", page_name: "Beta Blog", likes: 3, comments_count: 0, shares: 0 }),
    ];
    const { render: doRender } = renderTable({ posts, total: 2, loaded: true });
    doRender();

    expect(screen.getByText((_, element) => element?.textContent === "2 posts in dataset")).toBeInTheDocument();
    expect(screen.getByText("Alpha News")).toBeInTheDocument();
    expect(screen.getByText("Beta Blog")).toBeInTheDocument();
  });

  it("renders an empty state once loaded with no posts", () => {
    const { render: doRender } = renderTable({ loaded: true });
    doRender();
    expect(screen.getByText("No posts were extracted.")).toBeInTheDocument();
  });

  it("renders a loading skeleton before posts arrive", () => {
    const { render: doRender } = renderTable({ loading: true });
    doRender();
    expect(screen.getByLabelText("Loading posts")).toBeInTheDocument();
  });

  it("surfaces load errors with a retry action", () => {
    const { render: doRender, onRetry } = renderTable({ error: "Backend unreachable" });
    doRender();
    expect(screen.getByText("Could not load posts")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("filters posts by search query and clears the filter", () => {
    const posts = [
      makePost({ post_id: "p1", page_name: "Alpha", text: "solar cells launched" }),
      makePost({ post_id: "p2", page_name: "Beta", text: "wind turbine farm" }),
    ];
    const { render: doRender } = renderTable({ posts, total: 2, loaded: true });
    doRender();

    fireEvent.change(screen.getByLabelText("Search posts"), { target: { value: "solar" } });
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.queryByText("Beta")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /clear search/i }));
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
  });

  it("sorts by likes when the Likes header is clicked", () => {
    const posts = [
      makePost({ post_id: "p1", page_name: "Alpha", likes: 5 }),
      makePost({ post_id: "p2", page_name: "Beta", likes: 120 }),
    ];
    const { render: doRender } = renderTable({ posts, total: 2, loaded: true });
    doRender();

    fireEvent.click(screen.getByRole("button", { name: "Likes" }));
    // default direction is descending → highest likes first
    const rowButtons = screen.getAllByRole("button", { name: /open post details/i });
    expect(rowButtons[0]).toHaveTextContent("Beta");
    expect(rowButtons[1]).toHaveTextContent("Alpha");

    fireEvent.click(screen.getByRole("button", { name: "Likes" }));
    const rowButtonsAsc = screen.getAllByRole("button", { name: /open post details/i });
    expect(rowButtonsAsc[0]).toHaveTextContent("Alpha");
  });

  it("paginates with the default page size and navigates pages", () => {
    const posts = Array.from({ length: 25 }, (_, index) =>
      makePost({ post_id: `p${index}`, page_name: `Post ${index + 1}` })
    );
    const { render: doRender } = renderTable({ posts, total: 25, loaded: true });
    doRender();

    const byFullText = (text: string) => (_: string, element: Element | null) => element?.textContent === text;

    expect(screen.getByText(byFullText("Page 1 / 2"))).toBeInTheDocument();
    expect(screen.getByText(byFullText("Showing 1–20 of 25 filtered posts"))).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(screen.getByText(byFullText("Page 2 / 2"))).toBeInTheDocument();
    expect(screen.getByText(byFullText("Showing 21–25 of 25 filtered posts"))).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /previous/i }));
    expect(screen.getByText(byFullText("Page 1 / 2"))).toBeInTheDocument();
  });

  it("selects a post on row click", () => {
    const post = makePost({ post_id: "p1", page_name: "Alpha News" });
    const { render: doRender, onSelectPost } = renderTable({ posts: [post], total: 1, loaded: true });
    doRender();

    fireEvent.click(screen.getByRole("button", { name: /open post details from Alpha News/i }));
    expect(onSelectPost).toHaveBeenCalledWith(expect.objectContaining({ post_id: "p1" }));
  });

  it("selects a post on Enter keydown", () => {
    const post = makePost({ post_id: "p1", page_name: "Alpha News" });
    const { render: doRender, onSelectPost } = renderTable({ posts: [post], total: 1, loaded: true });
    doRender();

    fireEvent.keyDown(screen.getByRole("button", { name: /open post details from Alpha News/i }), {
      key: "Enter",
    });
    expect(onSelectPost).toHaveBeenCalledTimes(1);
  });

  it("renders a media thumbnail when the post has one", () => {
    const post = makePost({ thumbnail_url: "https://example.com/thumb.jpg" });
    const { render: doRender } = renderTable({ posts: [post], total: 1, loaded: true });
    doRender();

    const img = screen.getByAltText("") as HTMLImageElement;
    expect(img).toBeInTheDocument();
    expect(img.src).toContain("example.com/thumb.jpg");
  });

  it("renders a no-media placeholder otherwise", () => {
    const post = makePost({});
    const { render: doRender } = renderTable({ posts: [post], total: 1, loaded: true });
    doRender();

    expect(screen.getByLabelText("No media")).toBeInTheDocument();
  });
});