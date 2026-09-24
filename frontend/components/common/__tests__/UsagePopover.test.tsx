import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { User } from "firebase/auth";
import { UsagePopover } from "@/components/common/UsagePopover";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import type { AccountsResponse, UsageResponse, UserProfile } from "@/lib/types";

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { getUsage: vi.fn(), listAccounts: vi.fn() },
  ApiError: class ApiError extends Error {
    code: string;
    constructor(code: string, message: string) {
      super(message);
      this.name = "ApiError";
      this.code = code;
    }
  },
}));

const mockUseAuth = vi.mocked(useAuth);
const mockGetUsage = vi.mocked(api.getUsage);
const mockListAccounts = vi.mocked(api.listAccounts);

const USAGE: UsageResponse = {
  plan: "pro",
  jobs_running: { used: 0, limit: 3 },
  personal_accounts: { used: 2, limit: 5 },
  per_job: { urls: 50, max_posts: 5000 },
  posts_today: 0,
  jobs_today: 4,
};

const ACCOUNTS: AccountsResponse = {
  ops: [
    { name: "alpha", scope: "ops", status: "VALID" },
    { name: "beta", scope: "ops", status: "VALID" },
  ],
  mine: [],
};

function authValue(overrides: Partial<ReturnType<typeof useAuth>> = {}) {
  return {
    user: { email: "analyst@example.com" } as unknown as User,
    profile: { plan: "pro" } as unknown as UserProfile,
    loading: false,
    getIdToken: vi.fn(async () => "id-token"),
    refreshProfile: vi.fn(async () => undefined),
    signInWithGoogle: vi.fn(async () => undefined),
    signInWithEmail: vi.fn(async () => undefined),
    signUpWithEmail: vi.fn(async () => undefined),
    signOut: vi.fn(async () => undefined),
    ...overrides,
  };
}

/** Renders the pill (signed-in by default) and waits for the first fetch. */
async function renderPill(usage: UsageResponse = USAGE, accounts: AccountsResponse = ACCOUNTS) {
  mockGetUsage.mockResolvedValue(usage);
  mockListAccounts.mockResolvedValue(accounts);
  const view = render(<UsagePopover />);
  const pill = await screen.findByRole("button", { name: "Plan and usage" });
  await waitFor(() => expect(pill.textContent).toContain("Pool"));
  return { view, pill };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetUsage.mockResolvedValue(USAGE);
  mockListAccounts.mockResolvedValue(ACCOUNTS);
  mockUseAuth.mockReturnValue(authValue());
  document.documentElement.classList.remove("dark");
});

afterEach(() => {
  vi.useRealTimers();
  document.documentElement.classList.remove("dark");
});

describe("UsagePopover — pill", () => {
  it("renders nothing for signed-out visitors", () => {
    mockUseAuth.mockReturnValue(authValue({ user: null, profile: null }));
    render(<UsagePopover />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows the plan, session pool and concurrent jobs on the pill", async () => {
    const usage: UsageResponse = {
      ...USAGE,
      plan: "team",
      personal_accounts: { used: 3, limit: 25 },
      jobs_running: { used: 1, limit: 10 },
    };
    const { pill } = await renderPill(usage);
    expect(pill.textContent).toContain("Team");
    expect(pill.textContent).toContain("Pool 3/25");
    expect(pill.textContent).toContain("Jobs 1/10");
  });

  it("falls back to a neutral label before the first fetch lands", () => {
    mockGetUsage.mockReturnValue(new Promise(() => {})); // never resolves
    mockUseAuth.mockReturnValue(authValue());
    render(<UsagePopover />);
    expect(screen.getByRole("button", { name: "Plan and usage" })).toHaveTextContent("Usage");
  });
});

describe("UsagePopover — popover rows and the bounded/unbounded color rule", () => {
  it("opens on click with every row and the correct tone per value", async () => {
    const { pill, view } = await renderPill();
    fireEvent.click(pill);

    expect(screen.getByRole("dialog", { name: "Usage — Pro" })).toBeInTheDocument();
    expect(screen.getByText("Shared sessions")).toBeInTheDocument();
    expect(screen.getByText("Shared jobs today")).toBeInTheDocument();
    expect(screen.getByText("Personal sessions")).toBeInTheDocument();
    expect(screen.getByText(/Concurrent jobs/i)).toBeInTheDocument();
    expect(screen.getByText(/Pool availability/i)).toBeInTheDocument();
    expect(screen.getByText(/Resets daily at/i)).toBeInTheDocument();
    expect(screen.getByText("00:00 UTC")).toBeInTheDocument();

    const dds = Array.from(
      view.container.querySelectorAll<HTMLElement>('dd[data-tone]'),
    );
    expect(dds.map((dd) => ({ text: dd.textContent, tone: dd.getAttribute("data-tone") }))).toEqual([
      // 2 shared (ops) sessions, no ceiling — unbounded, plain primary
      { text: "2 of unlimited", tone: "unbounded" },
      // jobs today has no daily limit
      { text: "4/unlimited", tone: "unbounded" },
      // 2 used of 5 personal: 3/5 remaining -> healthy
      { text: "2 of 5", tone: "healthy" },
      // 0 of 3 concurrent > 50% remaining
      { text: "0 of 3", tone: "healthy" },
      // 3 concurrent slots free of 3
      { text: "3 free", tone: "healthy" },
    ]);
  });

  it("flips to warning and exhausted tones as headroom disappears", async () => {
    const usage: UsageResponse = {
      ...USAGE,
      jobs_running: { used: 2, limit: 3 }, // 1/3 remaining -> warning
      personal_accounts: { used: 5, limit: 5 }, // exhausted
    };
    const { pill, view } = await renderPill(usage);
    fireEvent.click(pill);

    const dds = Array.from(
      view.container.querySelectorAll<HTMLElement>('dd[data-tone]'),
    );
    const tones = dds.map((dd) => dd.getAttribute("data-tone"));
    expect(tones).toEqual([
      "unbounded",
      "unbounded",
      "exhausted", // 5 of 5 personal sessions
      "warning", // 1 of 3 concurrent headroom left
      "warning", // 1 free slot in the pool
    ]);
    const exhausted = dds.find((dd) => dd.textContent === "5 of 5");
    expect(exhausted?.className).toContain("text-usage-exhausted");
  });

  it("keeps popover open through a theme toggle with tokens intact", async () => {
    const { pill } = await renderPill();
    fireEvent.click(pill);
    const dialog = screen.getByRole("dialog");

    document.documentElement.classList.add("dark");
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(dialog.className).toContain("bg-usage-bg");
    expect(dialog.className).toContain("border-usage-border");
    // The value's count/denominator live in separate spans, so match the dd.
    const concurrent = Array.from(
      dialog.querySelectorAll<HTMLElement>("dd"),
    ).find((el) => el.textContent === "0 of 3");
    expect(concurrent?.className).toContain("text-usage-healthy");

    document.documentElement.classList.remove("dark");
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(dialog.className).toContain("bg-usage-bg");
  });
});

describe("UsagePopover — dismissal and focus", () => {
  it("closes on Escape and returns focus to the trigger pill", async () => {
    const { pill } = await renderPill();
    fireEvent.click(pill);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(pill);
  });

  it("closes via the Close button and returns focus to the trigger", async () => {
    const { pill } = await renderPill();
    fireEvent.click(pill);
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(pill);
  });

  it("closes on outside click without stealing focus from where the user aimed", async () => {
    const { pill } = await renderPill();
    fireEvent.click(pill);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.activeElement).not.toBe(pill);
  });

  it("traps Tab focus inside the dialog", async () => {
    const { pill } = await renderPill();
    fireEvent.click(pill);
    const refresh = screen.getByRole("button", { name: "Refresh usage" });
    const close = screen.getByRole("button", { name: /close/i });

    refresh.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(close); // wrap backwards

    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(refresh); // wrap forward
  });
});

describe("UsagePopover — refresh behavior", () => {
  it("spins the icon while refreshing and never stacks duplicate fetches on rapid clicks", async () => {
    const { pill } = await renderPill();
    fireEvent.click(pill);

    let resolveRefresh: (value: UsageResponse) => void = () => {};
    mockGetUsage.mockImplementationOnce(
      () => new Promise<UsageResponse>((resolve) => { resolveRefresh = resolve; }),
    );
    const refresh = screen.getByRole("button", { name: "Refresh usage" });

    expect(mockGetUsage).toHaveBeenCalledTimes(1); // mount fetch only
    fireEvent.click(refresh); // starts the deferred refresh
    await waitFor(() => {
      const icon = refresh.querySelector("svg");
      expect(icon?.getAttribute("class") ?? "").toContain("animate-spin");
    });

    fireEvent.click(refresh); // rapid click while in flight — must be ignored
    await act(async () => {
      resolveRefresh({ ...USAGE, jobs_today: 9 });
    });
    await waitFor(() => {
      const icon = refresh.querySelector("svg");
      expect(icon?.getAttribute("class") ?? "").not.toContain("animate-spin");
    });
    expect(mockGetUsage).toHaveBeenCalledTimes(2); // mount + exactly one refresh
  });

  it("surfaces an error instead of a permanent loader when the first fetch fails", async () => {
    mockGetUsage.mockRejectedValue(new Error("boom"));
    mockListAccounts.mockRejectedValue(new Error("boom"));
    mockUseAuth.mockReturnValue(authValue());
    render(<UsagePopover />);
    const pill = await screen.findByRole("button", { name: "Plan and usage" });
    // Let the rejected mount fetch settle so loading clears to the error state.
    await act(async () => {});
    // Pill falls back to a neutral label; opening shows the error state. The
    // open refetches (no snapshot yet), so flush that rejection too.
    expect(pill.textContent).toBe("Usage");
    fireEvent.click(pill);
    await act(async () => {});
    expect(screen.getByText(/couldn't load usage/i)).toBeInTheDocument();
  });
});

describe("UsagePopover — stale refetch on open", () => {
  it("refetches when the snapshot is older than 30s, not while fresh", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-24T12:00:00Z"));
    mockGetUsage.mockResolvedValue(USAGE);
    mockListAccounts.mockResolvedValue(ACCOUNTS);
    mockUseAuth.mockReturnValue(authValue());

    render(<UsagePopover />);
    const pill = screen.getByRole("button", { name: "Plan and usage" });
    // Flush the mount fetch (pure microtasks — no timer involved).
    await act(async () => {});
    await act(async () => {});
    expect(pill.textContent).toContain("Pool");
    expect(mockGetUsage).toHaveBeenCalledTimes(1);

    // Reopen while fresh: no refetch.
    fireEvent.click(pill);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    await act(async () => {});
    expect(mockGetUsage).toHaveBeenCalledTimes(1);

    // Age the snapshot past the 30s staleness window, then reopen.
    await act(async () => {
      vi.advanceTimersByTime(31_000);
    });
    await act(async () => {});
    fireEvent.click(pill);
    await act(async () => {});
    expect(mockGetUsage.mock.calls.length).toBeGreaterThan(1);
  });
});