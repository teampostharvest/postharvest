import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Dialog } from "@/components/ui/dialog";

function renderDialog(props: Partial<React.ComponentProps<typeof Dialog>> = {}) {
  const onClose = vi.fn();
  const view = render(
    <Dialog
      open
      onClose={onClose}
      title="Confirm export"
      description="Generate the CSV file?"
      footer={<button>Download</button>}
      {...props}
    >
      Body content
    </Dialog>
  );
  return { onClose, ...view };
}

describe("Dialog", () => {
  it("renders nothing when closed", () => {
    render(<Dialog open={false} onClose={vi.fn()}>hidden</Dialog>);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders title, description, body and footer once open", () => {
    renderDialog();
    expect(screen.getByRole("dialog", { name: "Confirm export" })).toBeInTheDocument();
    expect(screen.getByText("Generate the CSV file?")).toBeInTheDocument();
    expect(screen.getByText("Body content")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download" })).toBeInTheDocument();
  });

  it("closes via the explicit close button", () => {
    const { onClose } = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape keydown", () => {
    const { onClose } = renderDialog();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes when the backdrop is clicked", () => {
    const { onClose } = renderDialog();
    const dialog = screen.getByRole("dialog");
    const backdrop = dialog.firstElementChild as HTMLElement;
    expect(backdrop.className).toContain("bg-black/50");
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("locks body scroll while open and restores it after", async () => {
    document.body.style.overflow = "";
    const { unmount } = renderDialog();
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.keyDown(window, { key: "Escape" });
    unmount();
    await waitFor(() => {
      expect(document.body.style.overflow).toBe("");
    });
  });

  it("renders body content with no header when title is omitted", () => {
    render(<Dialog open onClose={vi.fn()}>Just body</Dialog>);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Just body")).toBeInTheDocument();
  });

  it("applies size classes for extra-large dialogs", () => {
    renderDialog({ size: "xl" });
    const dialog = screen.getByRole("dialog");
    const panel = dialog.querySelector(".sm\\:max-w-4xl");
    expect(panel).toBeInTheDocument();
  });
});