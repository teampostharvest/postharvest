import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Select } from "@/components/ui/select";

describe("Select", () => {
  it("renders a native select with its options", () => {
    render(
      <Select aria-label="Post type" defaultValue="video">
        <option value="">All</option>
        <option value="video">Video</option>
      </Select>
    );
    const select = screen.getByLabelText("Post type");
    expect(select).toHaveValue("video");
    expect(screen.getByRole("option", { name: "Video" })).toBeInTheDocument();
  });

  it("fires onChange with the selected value", () => {
    const onChange = vi.fn();
    render(
      <Select aria-label="Scrolls" onChange={onChange}>
        <option value="">None</option>
        <option value="10">10</option>
      </Select>
    );
    fireEvent.change(screen.getByLabelText("Scrolls"), { target: { value: "10" } });
    expect(onChange).toHaveBeenCalled();
  });

  it("applies the disabled state to the native element", () => {
    render(
      <Select aria-label="Scrolls" disabled>
        <option value="">None</option>
      </Select>
    );
    expect(screen.getByLabelText("Scrolls")).toBeDisabled();
  });

  it("merges custom classes with base classes", () => {
    render(
      <Select aria-label="Post type" className="w-64">
        <option value="">All</option>
      </Select>
    );
    const select = screen.getByLabelText("Post type");
    expect(select.className).toContain("w-64");
    expect(select.className).toContain("h-9");
  });
});