import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { MouseEvent, ReactNode } from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { OpsSidebar } from "@/components/common/OpsSidebar";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    onClick,
    ...props
  }: {
    href: string;
    children: ReactNode;
    onClick?: (event: MouseEvent) => void;
  }) => (
    <a href={href} onClick={onClick} {...props}>
      {children}
    </a>
  ),
}));

const { mockUseAuth } = vi.hoisted(() => ({
  mockUseAuth: vi.fn(),
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => mockUseAuth(),
}));

const ACCOUNTS = {
  ops: [{ name: "ops-pool", scope: "ops", status: "VALID" }],
  mine: [{ name: "personal", scope: "me", status: "EXPIRED" }],
};

const ACTIVE_JOBS = {
  items: [
    {
      job_id: "job-abc-123",
      status: "running",
      pages_total: 10,
      pages_completed: 4,
      posts_found: 40,
      posts_processed: 32,
      duplicates: 0,
      errors: 0,
      urls: ["https://www.facebook.com/ExamplePage"],
      created_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
      completed_at: null,
    },
  ],
  total: 1,
  page: 1,
  page_size: 25,
};

function stubFetch() {
  const fetchMock = vi.fn(async (url: unknown) => {
    const target = String(url);
    if (target.includes("/api/accounts")) {
      return { ok: true, status: 200, json: async () => ACCOUNTS };
    }
    return { ok: true, status: 200, json: async () => ACTIVE_JOBS };
  });
  vi.stubGlobal("fetch", fetchMock);
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  mockUseAuth.mockReturnValue({
    user: { email: "user@example.com" },
    profile: { plan: "team" },
    loading: false,
  });
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("OpsSidebar", () => {
  it("renders zero primary-navigation links", async () => {
    render(<OpsSidebar />);
    await waitFor(() => {
      expect(screen.getByLabelText("Active jobs")).toBeInTheDocument();
    });
    for (const label of ["Home", "Investigation", "Pricing", "History", "Settings", "Docs", "Saved accounts"]) {
      expect(screen.queryByRole("link", { name: label })).not.toBeInTheDocument();
    }
  });

  it("renders the ops sections with live data and no usage or plan blocks", async () => {
    render(<OpsSidebar />);
    await waitFor(() => {
      expect(screen.getByText("https://www.facebook.com/ExamplePage")).toBeInTheDocument();
    });
    expect(screen.getByLabelText("Active jobs")).toBeInTheDocument();
    expect(screen.getByLabelText("Saved accounts")).toBeInTheDocument();
    // Usage card is gone from the sidebar; plan lives on /pricing.
    expect(screen.queryByLabelText("Usage today")).not.toBeInTheDocument();
    expect(screen.queryByText("Jobs today")).not.toBeInTheDocument();
    expect(screen.queryByText("Posts today")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Plan")).not.toBeInTheDocument();
    // Active job row links to the live view.
    expect(screen.getByRole("link", { name: /ExamplePage.*open live view/ })).toHaveAttribute(
      "href",
      "/investigation?job=job-abc-123",
    );
  });

  it("shows category tree rows with counts and pastel badges, never a flat list", async () => {
    render(<OpsSidebar />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Active account for browser runs: ops-pool" })).toBeInTheDocument();
    });
    // Tree categories with live counts.
    expect(screen.getByRole("button", { name: /personal/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /ops pool/i })).toBeInTheDocument();
    // Individual accounts stay hidden until the picker/modal opens.
    expect(screen.queryByText("personal")).not.toBeInTheDocument();
    // Active pill carries the health badge inline.
    const pill = screen.getByRole("button", { name: "Active account for browser runs: ops-pool" });
    expect(pill).toHaveTextContent("healthy");
    expect(screen.getByRole("link", { name: "Add account" })).toHaveAttribute("href", "/accounts");
  });

  it("restores the stored active account explicitly in the pill", async () => {
    window.localStorage.setItem("postharvest-active-account", "me:personal");
    render(<OpsSidebar />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Active account for browser runs: personal" })).toBeInTheDocument();
    });
    window.localStorage.removeItem("postharvest-active-account");
  });

  it("shows empty states without fabricated data", async () => {
    const fetchMock = vi.fn(async (url: unknown) => {
      const target = String(url);
      if (target.includes("/api/accounts")) {
        return { ok: true, status: 200, json: async () => ({ ops: [], mine: [] }) };
      }
      return { ok: true, status: 200, json: async () => ({ items: [], total: 0, page: 1, page_size: 25 }) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<OpsSidebar />);
    await waitFor(() => {
      expect(screen.getByText("No jobs running")).toBeInTheDocument();
    });
    // Empty categories show zero counts; the pill invites selection.
    expect(screen.getByRole("button", { name: "Select account" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /personal/i })).toBeInTheDocument();
  });

  it("renders no collapse control — the toggle lives in the top bar", () => {
    render(<OpsSidebar />);
    expect(screen.queryByRole("button", { name: "Collapse panel" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Expand operations panel" })).not.toBeInTheDocument();
  });

  it("shows a sign-in note instead of stuck loaders when signed out", () => {
    mockUseAuth.mockReturnValue({ user: null, profile: null, loading: false });
    render(<OpsSidebar />);
    expect(screen.getByText(/sign in to see live jobs/i)).toBeInTheDocument();
    expect(screen.queryByText(/loading accounts/i)).not.toBeInTheDocument();
  });

  it("shows the active account pill without listing accounts flat", async () => {
    render(<OpsSidebar />);
    await waitFor(() => {
      // Falls back to the first healthy session when nothing is selected.
      expect(screen.getByRole("button", { name: "Active account for browser runs: ops-pool" })).toBeInTheDocument();
    });
    // Category tree with counts, not a flat account list.
    expect(screen.getByRole("button", { name: /personal/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /ops pool/i })).toBeInTheDocument();
    expect(screen.queryByText("personal")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /view all accounts/i })).toBeInTheDocument();
  });

  it("opens the picker, filters by search, and switches the active account", async () => {
    render(<OpsSidebar />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Active account for browser runs: ops-pool" })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Active account for browser runs: ops-pool" }));
    expect(screen.getByRole("option", { name: /personal/ })).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "Search accounts" }), {
      target: { value: "person" },
    });
    expect(screen.queryByRole("option", { name: /ops-pool/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("option", { name: /personal/ }));
    expect(window.localStorage.getItem("postharvest-active-account")).toBe("me:personal");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Active account for browser runs: personal" })).toBeInTheDocument();
    });
  });

  it("opens the searchable modal from the tree branch", async () => {
    render(<OpsSidebar />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Active account for browser runs: ops-pool" })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /view all accounts/i }));
    const dialog = await screen.findByRole("dialog", { name: "Select account" });
    expect(dialog).toHaveTextContent("Ops pool (1)");
    expect(dialog).toHaveTextContent("Personal (1)");

    fireEvent.click(screen.getByRole("option", { name: /personal/ }));
    expect(window.localStorage.getItem("postharvest-active-account")).toBe("me:personal");
  });

  it("shows a retry affordance instead of a permanent loader on failure", async () => {
    const fetchMock = vi.fn(async (url: unknown) => {
      const target = String(url);
      if (target.includes("/api/accounts")) {
        throw new Error("boom");
      }
      return { ok: true, status: 200, json: async () => ({ items: [], total: 0, page: 1, page_size: 25 }) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<OpsSidebar />);
    const retry = await screen.findByRole("button", { name: "Retry" });
    expect(screen.getByText(/couldn't load accounts/i)).toBeInTheDocument();
    const callsBefore = fetchMock.mock.calls.length;
    fireEvent.click(retry);
    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });
});
