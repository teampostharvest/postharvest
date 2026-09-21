import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { AvatarMenu } from "@/components/common/AvatarMenu";

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}));

import { useAuth } from "@/lib/auth-context";

const mockUseAuth = vi.mocked(useAuth);

function authValue(overrides: Partial<ReturnType<typeof useAuth>> = {}) {
  return {
    user: { email: "jane@example.com" },
    profile: { display_name: "Jane Doe", email: "jane@example.com", plan: "team", photo_url: null },
    loading: false,
    getIdToken: vi.fn(async () => "id-token"),
    refreshProfile: vi.fn(async () => undefined),
    signInWithGoogle: vi.fn(async () => undefined),
    signInWithEmail: vi.fn(async () => undefined),
    signUpWithEmail: vi.fn(async () => undefined),
    signOut: vi.fn(async () => undefined),
    ...overrides,
  } as unknown as ReturnType<typeof useAuth>;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AvatarMenu", () => {
  it("renders nothing when signed out", () => {
    mockUseAuth.mockReturnValue(authValue({ user: null, profile: null }));
    const { container } = render(<AvatarMenu />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the identity block with name, email and plan", () => {
    mockUseAuth.mockReturnValue(authValue());
    render(<AvatarMenu />);
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));

    const menu = screen.getByRole("menu", { name: "Account" });
    expect(menu).toHaveTextContent("Jane Doe");
    expect(menu).toHaveTextContent("jane@example.com");
    expect(menu).toHaveTextContent("Team plan");
  });

  it("lists Saved accounts, Settings and Sign out — and nothing else", () => {
    mockUseAuth.mockReturnValue(authValue());
    render(<AvatarMenu />);
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));

    expect(screen.getByRole("menuitem", { name: "Saved accounts" })).toHaveAttribute("href", "/accounts");
    expect(screen.getByRole("menuitem", { name: "Settings" })).toHaveAttribute("href", "/settings");
    expect(screen.getByRole("menuitem", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.getAllByRole("menuitem")).toHaveLength(3);
  });

  it("signs out when Sign out is clicked", () => {
    const signOut = vi.fn(async () => undefined);
    mockUseAuth.mockReturnValue(authValue({ signOut }));
    render(<AvatarMenu />);
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it("closes the menu on Escape", () => {
    mockUseAuth.mockReturnValue(authValue());
    render(<AvatarMenu />);
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("moves focus into the menu on open and back to the trigger on Escape", () => {
    mockUseAuth.mockReturnValue(authValue());
    render(<AvatarMenu />);
    const trigger = screen.getByRole("button", { name: "Account menu" });
    fireEvent.click(trigger);
    expect(screen.getByRole("menuitem", { name: "Saved accounts" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(trigger).toHaveFocus();
  });
});
