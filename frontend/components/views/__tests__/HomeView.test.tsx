import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { HomeView } from "@/components/views/HomeView";

describe("HomeView", () => {
  it("sends the normalized URL to onTrace for a valid input", () => {
    const onTrace = vi.fn();
    render(<HomeView onTrace={onTrace} />);

    const input = screen.getByPlaceholderText(/facebook\.com\/target/);
    fireEvent.change(input, { target: { value: "https://www.facebook.com/ExamplePage" } });
    fireEvent.click(screen.getByRole("button", { name: /scrape target/i }));

    expect(onTrace).toHaveBeenCalledTimes(1);
    expect(onTrace).toHaveBeenCalledWith("https://www.facebook.com/ExamplePage");
  });

  it("shows a validation error for a non-Facebook URL and does not trace", () => {
    const onTrace = vi.fn();
    render(<HomeView onTrace={onTrace} />);

    const input = screen.getByPlaceholderText(/facebook\.com\/target/);
    fireEvent.change(input, { target: { value: "https://twitter.com/example" } });
    fireEvent.click(screen.getByRole("button", { name: /scrape target/i }));

    expect(screen.getByText(/not a facebook\.com address/)).toBeInTheDocument();
    expect(onTrace).not.toHaveBeenCalled();
  });

  it("submits on Enter key", () => {
    const onTrace = vi.fn();
    render(<HomeView onTrace={onTrace} />);

    const input = screen.getByPlaceholderText(/facebook\.com\/target/);
    fireEvent.change(input, { target: { value: "https://www.facebook.com/ExamplePage" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onTrace).toHaveBeenCalledTimes(1);
  });

  it("offers the batch path via onAdvanced", () => {
    const onAdvanced = vi.fn();
    render(<HomeView onTrace={vi.fn()} onAdvanced={onAdvanced} />);

    fireEvent.click(screen.getByRole("button", { name: /just do it in batch/i }));
    expect(onAdvanced).toHaveBeenCalledTimes(1);
  });
});