import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Textarea } from "@/components/ui/textarea";

describe("Textarea", () => {
  it("renders a textarea element", () => {
    render(<Textarea aria-label="Targets" />);
    const textarea = screen.getByLabelText("Targets");
    expect(textarea.tagName).toBe("TEXTAREA");
  });

  it("passes value and onChange through", () => {
    const onChange = vi.fn();
    render(<Textarea aria-label="Targets" value="x" onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Targets"), { target: { value: "y" } });
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("respects disabled and placeholder props", () => {
    render(<Textarea aria-label="Targets" disabled placeholder="Paste URLs" />);
    const textarea = screen.getByLabelText("Targets");
    expect(textarea).toBeDisabled();
    expect(textarea).toHaveAttribute("placeholder", "Paste URLs");
  });

  it("merges custom classes with base classes", () => {
    render(<Textarea aria-label="Targets" className="max-h-40" />);
    const textarea = screen.getByLabelText("Targets");
    expect(textarea.className).toContain("max-h-40");
    expect(textarea.className).toContain("min-h-[96px]");
  });
});