import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Input } from "@/components/ui/input";

describe("Input", () => {
  it("renders a text input by default", () => {
    render(<Input aria-label="Target" />);
    const input = screen.getByLabelText("Target");
    expect(input.tagName).toBe("INPUT");
    expect(input).toHaveAttribute("type", "text");
  });

  it("honours an explicit type attribute", () => {
    render(<Input type="email" aria-label="Email" />);
    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
  });

  it("passes value and change events through", () => {
    const onChange = vi.fn();
    render(<Input aria-label="Target" value="abc" onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Target"), { target: { value: "xyz" } });
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("respects the disabled state", () => {
    render(<Input aria-label="Target" disabled />);
    expect(screen.getByLabelText("Target")).toBeDisabled();
  });

  it("merges custom classes with the base styles", () => {
    render(<Input aria-label="Target" className="w-1/2" />);
    const input = screen.getByLabelText("Target");
    expect(input.className).toContain("w-1/2");
    expect(input.className).toContain("h-9");
  });

  it("forwards placeholder text", () => {
    render(<Input aria-label="Target" placeholder="https://www.facebook.com/x" />);
    expect(screen.getByPlaceholderText("https://www.facebook.com/x")).toBeInTheDocument();
  });
});