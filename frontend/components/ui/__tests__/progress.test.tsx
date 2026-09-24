import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Progress } from "@/components/ui/progress";

describe("Progress", () => {
  it("renders a progressbar with the correct percentage", () => {
    render(<Progress value={50} />);
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
    expect(bar).toHaveAttribute("aria-valuenow", "50");
    expect(bar.querySelector("[style]")).toHaveStyle({ width: "50%" });
  });

  it("clamps values outside the 0-100 range", () => {
    render(<Progress value={250} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });

  it("treats negative values as zero", () => {
    render(<Progress value={-10} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
  });

  it("uses a custom max when provided", () => {
    render(<Progress value={30} max={40} />);
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuemax", "40");
    expect(bar).toHaveAttribute("aria-valuenow", "75");
  });

  it("renders an indeterminate bar when no value is supplied", () => {
    render(<Progress />);
    const bar = screen.getByRole("progressbar");
    expect(bar).not.toHaveAttribute("aria-valuenow");
    const indicator = bar.querySelector(".animate-indeterminate");
    expect(indicator).toBeInTheDocument();
  });

  it("renders an indeterminate bar when explicitly requested", () => {
    render(<Progress value={40} indeterminate />);
    expect(screen.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
  });

  it("forwards aria-label overrides", () => {
    render(<Progress value={1} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-label", "Job progress");
  });
});