import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import type { User } from "firebase/auth";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { SignInScreen } from "@/components/views/SignInScreen";
import { useAuth } from "@/lib/auth-context";

const { mockRouterReplace } = vi.hoisted(() => ({
  mockRouterReplace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockRouterReplace, push: vi.fn() }),
}));

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
    user: null,
    profile: null,
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
  mockRouterReplace.mockClear();
});

describe("SignInScreen", () => {
  it("renders a loading spinner while the auth session resolves", () => {
    mockUseAuth.mockReturnValue(authValue({ loading: true }));
    const { container } = render(<SignInScreen />);
    // The full-screen loader discards the action card entirely.
    expect(container.querySelector(".animate-spin")).toBeInTheDocument();
    expect(screen.queryByText("Continue with Google")).not.toBeInTheDocument();
  });

  it("signs in with email and password", async () => {
    const signInWithEmail = vi.fn(async () => undefined);
    mockUseAuth.mockReturnValue(authValue({ signInWithEmail }));
    render(<SignInScreen />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "agent@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter2" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(signInWithEmail).toHaveBeenCalledTimes(1);
    });
    expect(signInWithEmail).toHaveBeenCalledWith("agent@example.com", "hunter2");
  });

  it("signs up when the mode is toggled to create-account", async () => {
    const signUpWithEmail = vi.fn(async () => undefined);
    mockUseAuth.mockReturnValue(authValue({ signUpWithEmail }));
    render(<SignInScreen />);

    fireEvent.click(screen.getByRole("button", { name: /no account yet\? create one/i }));
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "new@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret1" } });
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => {
      expect(signUpWithEmail).toHaveBeenCalledWith("new@example.com", "secret1");
    });
  });

  it("signs in with Google", async () => {
    const signInWithGoogle = vi.fn(async () => undefined);
    mockUseAuth.mockReturnValue(authValue({ signInWithGoogle }));
    render(<SignInScreen />);

    fireEvent.click(screen.getByRole("button", { name: /continue with google/i }));

    await waitFor(() => {
      expect(signInWithGoogle).toHaveBeenCalledTimes(1);
    });
  });

  it("surfaces an error message when authentication fails", async () => {
    const signInWithEmail = vi.fn(async () => {
      throw new Error("Invalid credentials");
    });
    mockUseAuth.mockReturnValue(authValue({ signInWithEmail }));
    render(<SignInScreen />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "agent@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Invalid credentials")).toBeInTheDocument();
  });

  it("redirects a signed-in visitor to the dashboard", async () => {
    mockUseAuth.mockReturnValue(
      authValue({ user: { email: "agent@example.com" } as unknown as User })
    );
    render(<SignInScreen />);
    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith("/");
    });
  });

  it("returns a signed-in visitor to their pending hero target", async () => {
    window.sessionStorage.setItem("postharvest.pending-target", "https://www.facebook.com/ExamplePage");
    mockUseAuth.mockReturnValue(
      authValue({ user: { email: "agent@example.com" } as unknown as User })
    );
    render(<SignInScreen />);
    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith(
        "/investigation?url=https%3A%2F%2Fwww.facebook.com%2FExamplePage"
      );
    });
    expect(window.sessionStorage.getItem("postharvest.pending-target")).toBeNull();
  });

  it("ignores a non-URL pending target", async () => {
    window.sessionStorage.setItem("postharvest.pending-target", "javascript:alert(1)");
    mockUseAuth.mockReturnValue(
      authValue({ user: { email: "agent@example.com" } as unknown as User })
    );
    render(<SignInScreen />);
    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith("/");
    });
  });
});