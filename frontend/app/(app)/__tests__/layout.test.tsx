import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import AppShellLayout from "@/app/(app)/layout";

const { mockPathname } = vi.hoisted(() => ({
  mockPathname: vi.fn(() => "/"),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname() as string,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}));

vi.mock("@/components/common/ThemeProvider", () => ({
  useTheme: () => ({ theme: "light", toggleTheme: vi.fn(), setTheme: vi.fn() }),
}));

vi.mock("@/components/common/OpsSidebar", () => ({
  OpsSidebar: () => <div data-testid="ops-sidebar" />,
}));

vi.mock("@/components/common/AvatarMenu", () => ({
  AvatarMenu: () => null,
}));

vi.mock("@/components/views/SignInScreen", () => ({
  SignInScreen: () => <div data-testid="sign-in" />,
}));

import { useAuth } from "@/lib/auth-context";

const mockUseAuth = vi.mocked(useAuth);

function authValue(user: unknown) {
  return {
    user: user as null,
    profile: null,
    loading: false,
    getIdToken: vi.fn(async () => "id-token"),
    refreshProfile: vi.fn(async () => undefined),
    signInWithGoogle: vi.fn(async () => undefined),
    signInWithEmail: vi.fn(async () => undefined),
    signUpWithEmail: vi.fn(async () => undefined),
    signOut: vi.fn(async () => undefined),
  } as unknown as ReturnType<typeof useAuth>;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockPathname.mockReturnValue("/");
  window.localStorage.clear();
});

describe("AppShellLayout", () => {
  it("renders no sidebar rail when logged out, but keeps mobile navigation", () => {
    mockUseAuth.mockReturnValue(authValue(null));
    render(
      <AppShellLayout>
        <div>child</div>
      </AppShellLayout>
    );

    expect(screen.queryByTestId("ops-sidebar")).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    // Public content still renders, with Sign In in the top bar.
    expect(screen.getByText("child")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign In" })).toBeInTheDocument();
    // The wordmark is the single home affordance — no duplicate Home link.
    expect(screen.getByRole("link", { name: "PostHarvest home" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("link", { name: "Home" })).not.toBeInTheDocument();

    // The hamburger stays so small screens keep primary navigation —
    // opening it shows nav links (alongside the CSS-hidden desktop row)
    // but no ops panel.
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(screen.getAllByRole("link", { name: "Pricing" })).toHaveLength(2);
    expect(screen.queryByTestId("ops-sidebar")).not.toBeInTheDocument();
  });

  it("renders the ops sidebar when signed in", () => {
    mockUseAuth.mockReturnValue(authValue({ email: "user@example.com" }));
    render(
      <AppShellLayout>
        <div>child</div>
      </AppShellLayout>
    );

    expect(screen.getByTestId("ops-sidebar")).toBeInTheDocument();
  });

  it("keeps the toggle in the top bar at the same spot when collapsed", () => {
    window.localStorage.setItem("postharvest.sidebar-collapsed", "1");
    mockUseAuth.mockReturnValue(authValue({ email: "user@example.com" }));
    render(
      <AppShellLayout>
        <div>child</div>
      </AppShellLayout>
    );

    // No rail, no column, no floating button — the top-bar toggle stays put
    // and only its icon flips to expand.
    expect(screen.queryByTestId("ops-sidebar")).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "Expand operations panel" });
    expect(toggle).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("child")).toBeInTheDocument();
  });

  it("shows the collapse toggle in the top bar when expanded", () => {
    mockUseAuth.mockReturnValue(authValue({ email: "user@example.com" }));
    render(
      <AppShellLayout>
        <div>child</div>
      </AppShellLayout>
    );

    const toggle = screen.getByRole("button", { name: "Collapse operations panel" });
    expect(toggle).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });
});
