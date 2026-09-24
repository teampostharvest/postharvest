import { renderHook, waitFor, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "@/lib/api";
import { useJobPosts, useJobProgress } from "@/lib/hooks";
import type { JobProgress, Post } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getJob: vi.fn(),
      getPosts: vi.fn(),
    },
  };
});

const mockedApi = vi.mocked(api);

beforeEach(() => {
  vi.clearAllMocks();
});

function mockPost(id: string): Post {
  return {
    post_id: id,
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
  };
}

describe("useJobProgress", () => {
  it("returns null job while no job id is provided", () => {
    const { result } = renderHook(() => useJobProgress(null));
    expect(result.current.job).toBeNull();
    expect(mockedApi.getJob).not.toHaveBeenCalled();
  });

  it("polls the job endpoint and surfaces the current status", async () => {
    mockedApi.getJob.mockResolvedValue({
      job_id: "job-1",
      status: "running",
      pages_total: 2,
      pages_completed: 1,
      posts_found: 10,
      posts_processed: 4,
      duplicates: 0,
      errors: 0,
    } as JobProgress);

    const { result } = renderHook(() => useJobProgress("job-1", { pollMs: 25 }));

    await waitFor(() => expect(result.current.job?.status).toBe("running"));
    expect(result.current.error).toBeNull();
    expect(mockedApi.getJob).toHaveBeenCalledWith("job-1");
  });

  it("stops polling once the job reaches a terminal state", async () => {
    mockedApi.getJob.mockResolvedValue({
      job_id: "job-1",
      status: "completed",
      pages_total: 1,
      pages_completed: 1,
      posts_found: 3,
      posts_processed: 3,
      duplicates: 0,
      errors: 0,
    } as JobProgress);

    const { result } = renderHook(() => useJobProgress("job-1", { pollMs: 10 }));

    await waitFor(() => expect(result.current.job?.status).toBe("completed"));
    const calls = mockedApi.getJob.mock.calls.length;
    // Give the loop a beat to prove no further polling happens.
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(mockedApi.getJob.mock.calls.length).toBe(calls);
  });

  it("exposes an ApiError when the request fails", async () => {
    mockedApi.getJob.mockRejectedValue(new ApiError({ code: "network_error", message: "Backend down" }));

    const { result } = renderHook(() => useJobProgress("job-1", { pollMs: 25 }));

    await waitFor(() => expect(result.current.error).toBeInstanceOf(ApiError));
    expect(result.current.error?.message).toBe("Backend down");
  });

  it("retry() forces an immediate re-poll", async () => {
    mockedApi.getJob
      .mockRejectedValueOnce(new ApiError({ code: "network_error", message: "Backend down" }))
      .mockResolvedValue({
        job_id: "job-1",
        status: "completed",
        pages_total: 1,
        pages_completed: 1,
        posts_found: 1,
        posts_processed: 1,
        duplicates: 0,
        errors: 0,
      } as JobProgress);

    const { result } = renderHook(() => useJobProgress("job-1", { pollMs: 1000 }));
    await waitFor(() => expect(result.current.error).toBeInstanceOf(ApiError));

    act(() => result.current.retry());
    await waitFor(() => expect(result.current.job?.status).toBe("completed"));
    expect(result.current.error).toBeNull();
  });
});

describe("useJobPosts", () => {
  it("walks the paginated endpoint and collects all posts", async () => {
    const items = [mockPost("p1"), mockPost("p2"), mockPost("p3")];
    mockedApi.getPosts.mockImplementation(async (_jobId, params) => ({
      items: params?.page === 1 ? items.slice(0, 2) : items.slice(2),
      total: 3,
      page: params?.page ?? 1,
      page_size: params?.page_size ?? 200,
    }));

    const { result } = renderHook(() => useJobPosts("job-1"));
    await waitFor(() => expect(result.current.loaded).toBe(true));

    expect(result.current.posts).toHaveLength(3);
    expect(result.current.total).toBe(3);
    expect(result.current.capped).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("caps results at maxPosts and flags the cap", async () => {
    const items = Array.from({ length: 5 }, (_, index) => mockPost(`p${index + 1}`));
    mockedApi.getPosts.mockImplementation(async (_jobId, params) => {
      const page = params?.page ?? 1;
      const start = (page - 1) * 2;
      return {
        items: items.slice(start, start + 2),
        total: 5,
        page,
        page_size: params?.page_size ?? 200,
      };
    });

    const { result } = renderHook(() => useJobPosts("job-1", { pageSize: 2, maxPosts: 3 }));
    await waitFor(() => expect(result.current.loaded).toBe(true));

    expect(result.current.posts.length).toBeGreaterThanOrEqual(3);
    expect(result.current.capped).toBe(true);
  });

  it("wraps non-API errors into ApiError", async () => {
    mockedApi.getPosts.mockRejectedValue(new Error("boom"));

    const { result } = renderHook(() => useJobPosts("job-1"));
    await waitFor(() => expect(result.current.error).toBeInstanceOf(ApiError));
    expect(result.current.error?.code).toBe("network_error");
    expect(result.current.loaded).toBe(false);
  });

  it("reload() refetches the post list", async () => {
    mockedApi.getPosts.mockResolvedValue({ items: [mockPost("p1")], total: 1, page: 1, page_size: 200 });

    const { result } = renderHook(() => useJobPosts("job-1"));
    await waitFor(() => expect(result.current.loaded).toBe(true));

    expect(mockedApi.getPosts).toHaveBeenCalledTimes(1);
    act(() => result.current.reload());
    await waitFor(() => expect(mockedApi.getPosts.mock.calls.length).toBeGreaterThanOrEqual(2));
  });
});