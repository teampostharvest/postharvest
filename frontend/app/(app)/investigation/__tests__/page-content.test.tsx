import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import InvestigationPage from "@/app/(app)/investigation/page-content";
import { api } from "@/lib/api";

const { mockSearchGet } = vi.hoisted(() => ({
  mockSearchGet: vi.fn((key: string): string | null => {
    void key;
    return null;
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/investigation",
  useSearchParams: () => ({ get: (key: string) => mockSearchGet(key) }),
}));

vi.mock("@/lib/settings", () => ({
  readScrapeDefaults: vi.fn(() => ({
    maxPosts: "",
    postType: "" as const,
    scrolls: "",
    useBrowser: false,
  })),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listAccounts: vi.fn(async () => ({ ops: [], mine: [] })),
      getJob: vi.fn(),
    },
  };
});

const mockApi = vi.mocked(api);

beforeEach(() => {
  vi.clearAllMocks();
  mockSearchGet.mockImplementation(() => null);
  mockApi.getJob.mockImplementation(() => new Promise(() => {}));
});

describe("InvestigationPage", () => {
  it("renders exactly one headline-level element in the idle state", () => {
    render(<InvestigationPage />);
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Target, configure, run.");
  });

  it("holds the idle headline while a deep-linked job is still loading", () => {
    mockSearchGet.mockImplementation((key: string) => (key === "job" ? "job-123" : null));
    render(<InvestigationPage />);
    // No "Scraper Running" flash before the status arrives.
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Target, configure, run.");
    expect(mockApi.getJob).toHaveBeenCalledWith("job-123");
  });
});
