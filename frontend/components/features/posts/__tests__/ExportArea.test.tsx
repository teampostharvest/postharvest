import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { ExportArea } from "@/components/features/posts/ExportArea";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    exportJobDownload: vi.fn(),
  },
}));

describe("ExportArea", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not render when job is active or missing", () => {
    const { container: c1 } = render(
      <ExportArea jobId={null} status="completed" onNewScrape={vi.fn()} />
    );
    expect(c1.firstChild).toBeNull();

    const { container: c2 } = render(
      <ExportArea jobId="job-1" status="running" onNewScrape={vi.fn()} />
    );
    expect(c2.firstChild).toBeNull();
  });

  it("renders export options when job reaches completed status", () => {
    render(
      <ExportArea jobId="job-1" status="completed" onNewScrape={vi.fn()} />
    );

    expect(screen.getByText("Export results")).toBeInTheDocument();
    expect(screen.getByText("facebook_posts.json")).toBeInTheDocument();
    expect(screen.getByText("facebook_posts.csv")).toBeInTheDocument();
    expect(screen.getByText("facebook_posts.xlsx")).toBeInTheDocument();
  });

  it("calls api.exportJobDownload on format button click", async () => {
    vi.mocked(api.exportJobDownload).mockResolvedValueOnce(undefined);
    render(
      <ExportArea jobId="job-abc" status="completed" onNewScrape={vi.fn()} />
    );

    fireEvent.click(screen.getByText("facebook_posts.json"));
    expect(api.exportJobDownload).toHaveBeenCalledWith("job-abc", "json");
  });

  it("triggers onNewScrape when New scrape button is clicked", () => {
    const onNew = vi.fn();
    render(
      <ExportArea jobId="job-abc" status="completed" onNewScrape={onNew} />
    );

    fireEvent.click(screen.getByRole("button", { name: /new scrape/i }));
    expect(onNew).toHaveBeenCalledTimes(1);
  });
});
