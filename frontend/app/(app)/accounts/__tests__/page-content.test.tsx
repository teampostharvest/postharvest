import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import AccountsPage from "@/app/(app)/accounts/page-content";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { AccountsResponse, UserProfile } from "@/lib/types";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listAccounts: vi.fn(),
      addCookiesTxt: vi.fn(),
      startSessionCapture: vi.fn(),
      cancelSessionCapture: vi.fn(),
      deleteAccount: vi.fn(),
    },
  };
});

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}));

const mockApi = vi.mocked(api);
const mockUseAuth = vi.mocked(useAuth);

const SESSIONS: AccountsResponse = {
  ops: [{ name: "shared-a", scope: "ops", status: "VALID" }],
  mine: [{ name: "personal-a", scope: "me", status: "EXPIRED" }],
};

const COOKIES_TXT =
  "# Netscape HTTP Cookie File\n" +
  ".facebook.com\tTRUE\t/\tFALSE\t0\twd\t1234\n" +
  "#HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t0\tc_user\t1000001\n" +
  "#HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t0\txs\tsecret\n";

function authValue(overrides: Partial<ReturnType<typeof useAuth>> = {}) {
  return {
    user: null,
    profile: { plan: "basic" } as unknown as UserProfile,
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
  mockApi.listAccounts.mockResolvedValue(SESSIONS);
  mockApi.addCookiesTxt.mockResolvedValue({
    name: "pasted",
    scope: "me",
    status: "VALID",
  });
  mockUseAuth.mockReturnValue(authValue());
});

async function openPasteDialog() {
  render(<AccountsPage />);
  await waitFor(() => expect(mockApi.listAccounts).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Add my session" }));
  await waitFor(() =>
    expect(screen.getByRole("group", { name: "How to add the session" })).toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Paste cookies.txt" }));
  await waitFor(() => expect(screen.getByLabelText("cookies.txt")).toBeInTheDocument());
}

describe("AccountsPage", () => {
  it("lists ops-pool and personal sessions from the API", async () => {
    render(<AccountsPage />);
    await waitFor(() => expect(mockApi.listAccounts).toHaveBeenCalledTimes(1));
    expect(screen.getByText("shared-a")).toBeInTheDocument();
    expect(screen.getByText("personal-a")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Operator pool" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "My sessions" })).toBeInTheDocument();
  });

  it("adds a session from pasted cookies.txt and refreshes the list", async () => {
    await openPasteDialog();

    fireEvent.change(screen.getByLabelText("Session name"), { target: { value: "pasted" } });
    fireEvent.change(screen.getByLabelText("cookies.txt"), { target: { value: COOKIES_TXT } });
    fireEvent.click(screen.getByRole("button", { name: "Save session" }));

    await waitFor(() =>
      expect(mockApi.addCookiesTxt).toHaveBeenCalledWith({
        name: "pasted",
        scope: "me",
        cookies_txt: COOKIES_TXT,
      }),
    );
    // List refreshed after save, dialog closed.
    await waitFor(() => expect(mockApi.listAccounts).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("disables save until name and cookies are provided", async () => {
    await openPasteDialog();

    const save = screen.getByRole("button", { name: "Save session" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Session name"), { target: { value: "pasted" } });
    expect((screen.getByRole("button", { name: "Save session" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("cookies.txt"), { target: { value: COOKIES_TXT } });
    expect((screen.getByRole("button", { name: "Save session" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("opens the paste dialog directly from the Add Cookies.txt button", async () => {
    render(<AccountsPage />);
    await waitFor(() => expect(mockApi.listAccounts).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "Add Cookies.txt" }));

    // Paste mode is selected and the cookies.txt field is visible immediately —
    // no need to click the "Paste cookies.txt" toggle first.
    expect(screen.getByRole("button", { name: "Paste cookies.txt" })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(screen.getByLabelText("cookies.txt")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Save session" })).toBeInTheDocument();
  });

  it("surfaces the server error when the jar is rejected", async () => {
    mockApi.addCookiesTxt.mockRejectedValue(new Error("missing xs cookie"));
    await openPasteDialog();

    fireEvent.change(screen.getByLabelText("Session name"), { target: { value: "pasted" } });
    fireEvent.change(screen.getByLabelText("cookies.txt"), { target: { value: COOKIES_TXT } });
    fireEvent.click(screen.getByRole("button", { name: "Save session" }));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("missing xs cookie"));
    // Dialog stays open so the user can fix the export.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(mockApi.listAccounts).toHaveBeenCalledTimes(1);
  });
});