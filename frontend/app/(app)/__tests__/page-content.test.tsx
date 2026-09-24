import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import HomePage from "@/app/(app)/page-content";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const { mockRouterPush } = vi.hoisted(() => ({
  mockRouterPush: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockRouterPush, replace: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => ({ get: () => null }),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listJobs: vi.fn(),
      getUsage: vi.fn(),
    },
  };
});

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}));

const mockApi = vi.mocked(api);
const mockUseAuth = vi.mocked(useAuth);

function authValue(user: { email?: string | null } | null) {
  return {
    user: user as unknown as null,
    profile: user ? ({ plan: "pro" } as unknown as null) : null,
    loading: false,
    getIdToken: vi.fn(async () => "id-token"),
    refreshProfile: vi.fn(async () => undefined),
    signInWithGoogle: vi.fn(async () => undefined),
    signInWithEmail: vi.fn(async () => undefined),
    signUpWithEmail: vi.fn(async () => undefined),
    signOut: vi.fn(async () => undefined),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockApi.listJobs.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 5 });
  mockApi.getUsage.mockResolvedValue({
    plan: "pro",
    jobs_running: { used: 0, limit: 3 },
    personal_accounts: { used: 0, limit: 5 },
    per_job: { urls: 50, max_posts: 5000 },
    posts_today: 0,
    jobs_today: 0,
  });
});

describe("HomePage", () => {
  it("renders the hero markup when signed out", () => {
    mockUseAuth.mockReturnValue(authValue(null));
    render(<HomePage />);
    expect(screen.getByRole("heading", { name: /facebook scraping,fully automated/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("Quick launch")).not.toBeInTheDocument();
  });

  it("renders the dashboard markup when authenticated", async () => {
    mockUseAuth.mockReturnValue(authValue({ email: "user@example.com" }));
    render(<HomePage />);
    await waitFor(() => {
      expect(screen.getByLabelText("Quick launch")).toBeInTheDocument();
    });
    expect(screen.getByLabelText("Recent jobs")).toBeInTheDocument();
    expect(screen.getByLabelText("Today's usage")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /facebook scraping/i })).not.toBeInTheDocument();
  });

  it("reveals recent targets on focus and launches from the sheet", async () => {
    mockApi.listJobs.mockResolvedValue({
      items: [
        {
          job_id: "job-1",
          status: "completed",
          pages_total: 1,
          pages_completed: 1,
          posts_found: 5,
          posts_processed: 5,
          duplicates: 0,
          errors: 0,
          urls: ["https://www.facebook.com/ExamplePage"],
          created_at: null,
          completed_at: null,
        },
      ],
      total: 1,
      page: 1,
      page_size: 5,
    });
    mockUseAuth.mockReturnValue(authValue({ email: "user@example.com" }));
    render(<HomePage />);
    await waitFor(() => {
      expect(screen.getByLabelText("Quick launch")).toBeInTheDocument();
    });

    // Sheet hidden until focus.
    expect(screen.queryByLabelText("Recent targets")).not.toBeInTheDocument();
    fireEvent.focus(screen.getByLabelText("Target Facebook URL"));
    const sheet = await screen.findByLabelText("Recent targets");
    expect(sheet).toHaveTextContent("facebook.com/ExamplePage");
  });

  it("shakes the pill and shows an error on invalid submit", async () => {
    mockUseAuth.mockReturnValue(authValue({ email: "user@example.com" }));
    const { container } = render(<HomePage />);
    await waitFor(() => {
      expect(screen.getByLabelText("Quick launch")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText("Target Facebook URL"), {
      target: { value: "https://twitter.com/nope" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Launch investigation" }));

    expect(await screen.findByText(/not a facebook\.com address/)).toBeInTheDocument();
    expect(container.querySelector(".animate-shake")).toBeInTheDocument();
  });

  it("stashes the hero target so login cannot drop it", () => {
    mockUseAuth.mockReturnValue(authValue(null));
    render(<HomePage />);

    fireEvent.change(screen.getByPlaceholderText(/facebook\.com\/target/), {
      target: { value: "https://www.facebook.com/ExamplePage" },
    });
    fireEvent.click(screen.getByRole("button", { name: /scrape target/i }));

    expect(mockRouterPush).toHaveBeenCalledWith(
      "/investigation?url=https%3A%2F%2Fwww.facebook.com%2FExamplePage"
    );
    expect(window.sessionStorage.getItem("postharvest.pending-target")).toBe(
      "https://www.facebook.com/ExamplePage"
    );
    window.sessionStorage.removeItem("postharvest.pending-target");
  });
});
