import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { ReactNode } from "react";
import { AccountCard } from "@/components/common/AccountCard";
import { useAuth } from "@/lib/auth-context";
import type { User } from "firebase/auth";
import type { UserProfile } from "@/lib/types";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}));

const mockUseAuth = vi.mocked(useAuth);

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

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AccountCard", () => {
  it("renders nothing for signed-out visitors", () => {
    mockUseAuth.mockReturnValue(authValue({ user: null, profile: null }));
    render(<AccountCard />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows the display name and plan label when signed in", () => {
    mockUseAuth.mockReturnValue(
      authValue({
        user: { email: "analyst@example.com" } as unknown as User,
        profile: { display_name: "Analyst One", plan: "enterprise" } as unknown as UserProfile,
      })
    );
    render(<AccountCard />);
    expect(screen.getByText("Analyst One")).toBeInTheDocument();
    expect(screen.getByText(/enterprise plan/i)).toBeInTheDocument();
  });

  it("falls back to the email prefix when no display name exists", () => {
    mockUseAuth.mockReturnValue(
      authValue({
        user: { email: "analyst@example.com" } as unknown as User,
        profile: { display_name: null, plan: "basic" } as unknown as UserProfile,
      })
    );
    render(<AccountCard />);
    expect(screen.getByText("analyst")).toBeInTheDocument();
    expect(screen.getByText(/basic plan/i)).toBeInTheDocument();
  });

  it("falls back to the Free label for unknown plan values", () => {
    mockUseAuth.mockReturnValue(
      authValue({
        user: { email: "analyst@example.com" } as unknown as User,
        profile: { display_name: null, plan: "unknown-tier" } as unknown as UserProfile,
      })
    );
    render(<AccountCard />);
    expect(screen.getByText(/free plan/i)).toBeInTheDocument();
  });

  it("signs out when the sign-out button is clicked", () => {
    const signOut = vi.fn(async () => undefined);
    mockUseAuth.mockReturnValue(authValue({ signOut }));
    render(<AccountCard />);

    fireEvent.click(screen.getByRole("button", { name: /sign out/i }));
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it("links to the pricing page to change plan", () => {
    mockUseAuth.mockReturnValue(authValue());
    render(<AccountCard />);
    expect(screen.getByRole("link", { name: /change plan/i })).toHaveAttribute("href", "/pricing");
  });
});