import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { HoverPeekPanel } from "@/components/common/HoverPeekPanel";

vi.mock("@/components/common/OpsSidebar", () => ({
  OpsSidebar: () => <div data-testid="ops-panel">panel</div>,
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    user: { email: "user@example.com" },
    profile: { plan: "team" },
    loading: false,
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe("HoverPeekPanel", () => {
  it("reveals the panel on edge hover and dismisses on leave", async () => {
    render(<HoverPeekPanel />);
    expect(screen.queryByTestId("ops-panel")).not.toBeInTheDocument();

    fireEvent.mouseEnter(screen.getByTestId("peek-zone"));
    expect(await screen.findByTestId("ops-panel")).toBeInTheDocument();

    fireEvent.mouseLeave(screen.getByLabelText("Operations panel preview"));
    await waitFor(() => {
      expect(screen.queryByTestId("ops-panel")).not.toBeInTheDocument();
    });
  });

  it("stays open while the pointer moves onto the panel", async () => {
    render(<HoverPeekPanel />);
    fireEvent.mouseEnter(screen.getByTestId("peek-zone"));
    const panel = await screen.findByTestId("ops-panel");

    fireEvent.mouseEnter(screen.getByLabelText("Operations panel preview"));
    // Wait past the close grace delay — the panel must survive.
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(panel).toBeInTheDocument();
  });

  it("dismisses on Escape", async () => {
    render(<HoverPeekPanel />);
    fireEvent.mouseEnter(screen.getByTestId("peek-zone"));
    await screen.findByTestId("ops-panel");

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByTestId("ops-panel")).not.toBeInTheDocument();
    });
  });
});
