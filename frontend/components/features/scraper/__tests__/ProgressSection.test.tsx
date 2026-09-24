import { describe, expect, it } from "vitest";
import { computeEtaSeconds, computePercent, formatEta } from "@/components/features/scraper/ProgressSection";
import type { JobProgress } from "@/lib/types";

function makeJob(overrides: Partial<JobProgress> = {}): JobProgress {
  return {
    status: "running",
    pages_total: 1,
    pages_completed: 0,
    posts_found: 100,
    posts_processed: 0,
    duplicates: 0,
    errors: 0,
    max_posts: 100,
    ...overrides,
  };
}

describe("computePercent (D13)", () => {
  it("shows 100% once completed", () => {
    expect(computePercent(makeJob({ status: "completed" }))).toBe(100);
  });

  it("steps by page for multi-source jobs", () => {
    const job = makeJob({ pages_total: 4, pages_completed: 1 });
    expect(computePercent(job)).toBe(25);
  });

  it("returns a live fraction once posts start flowing", () => {
    const job = makeJob({ posts_found: 100, posts_processed: 40 });
    expect(computePercent(job)).toBe(40);
  });

  it("keeps the bar moving during discovery via posts_found/max_posts (Q10-B)", () => {
    const job = makeJob({ posts_found: 18, posts_processed: 0, max_posts: 60 });
    expect(computePercent(job)).toBe(30);
  });

  it("returns null while discovering without a max_posts cap (honest indeterminate)", () => {
    const job = makeJob({ posts_found: 18, posts_processed: 0, max_posts: null, pages_total: 0 });
    expect(computePercent(job)).toBeNull();
  });

  it("clamps to 100 and never exceeds it", () => {
    const job = makeJob({ posts_found: 10, posts_processed: 15 });
    expect(computePercent(job)).toBe(100);
  });

  it("reports 0 on failure with no posts", () => {
    expect(computePercent(makeJob({ status: "failed", posts_found: 0, posts_processed: 0 }))).toBe(0);
  });
});

describe("computeEtaSeconds (D13, Q5-A)", () => {
  it("bases the estimate on the posts_processed rate vs remaining", () => {
    const job = makeJob({ posts_found: 100, posts_processed: 50 });
    // 50 processed / 25s => 2/s ; 50 remaining => 25s
    expect(computeEtaSeconds(25, job)).toBe(25);
  });

  it("falls back to posts_found vs max_posts during discovery", () => {
    const job = makeJob({ posts_found: 40, posts_processed: 0, max_posts: 60 });
    // 40 found / 20s => 2/s ; 20 remaining => 10s
    expect(computeEtaSeconds(20, job)).toBe(10);
  });

  it("returns null when there is no signal to extrapolate from", () => {
    expect(computeEtaSeconds(10, makeJob({ posts_found: 0, posts_processed: 0 }))).toBeNull();
  });
});

describe("formatEta (D13, Q7-A)", () => {
  it("renders minutes and seconds", () => {
    expect(formatEta(204)).toBe("3m 24s");
    expect(formatEta(30)).toBe("0m 30s");
  });

  it("renders hours once a job exceeds an hour", () => {
    expect(formatEta(3720)).toBe("1h 2m");
  });

  it("never produces a negative estimate", () => {
    expect(formatEta(-5)).toBe("0m 0s");
  });
});
describe("ProgressSection titles", () => {
  it("shows a loading title while the job has not arrived yet", async () => {
    const { render, screen } = await import("@testing-library/react");
    const { ProgressSection } = await import("@/components/features/scraper/ProgressSection");
    render(<ProgressSection active={true} job={null} error={null} onRetry={() => {}} />);
    expect(screen.getByText("Loading run")).toBeInTheDocument();
    expect(screen.queryByText("Scraping in progress")).not.toBeInTheDocument();
  });

  it("shows a completed title for finished runs, never in-progress", async () => {
    const { render, screen } = await import("@testing-library/react");
    const { ProgressSection } = await import("@/components/features/scraper/ProgressSection");
    render(
      <ProgressSection
        active={false}
        job={makeJob({ status: "completed", pages_completed: 1, posts_processed: 100 })}
        error={null}
        onRetry={() => {}}
      />
    );
    expect(screen.getByText("Run complete")).toBeInTheDocument();
    expect(screen.queryByText("Scraping in progress")).not.toBeInTheDocument();
  });
});
