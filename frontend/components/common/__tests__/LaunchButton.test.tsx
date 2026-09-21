import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LaunchButton } from "@/components/common/LaunchButton";

describe("LaunchButton", () => {
  it("renders the label with an animated icon and fires on click", () => {
    const onClick = vi.fn();
    render(<LaunchButton onClick={onClick}>Start Scraping</LaunchButton>);
    const button = screen.getByRole("button", { name: "Start Scraping" });
    expect(button).toBeInTheDocument();
    expect(button.className).toContain("rounded-full");
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("shows the loading label and spinner while busy", () => {
    render(
      <LaunchButton onClick={() => {}} loading loadingLabel="Starting…">
        Start Scraping
      </LaunchButton>
    );
    const button = screen.getByRole("button", { name: "Starting…" });
    expect(button).toBeDisabled();
  });

  it("disables interaction when disabled", () => {
    const onClick = vi.fn();
    render(
      <LaunchButton onClick={onClick} disabled>
        Start Scraping
      </LaunchButton>
    );
    const button = screen.getByRole("button", { name: "Start Scraping" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
});
