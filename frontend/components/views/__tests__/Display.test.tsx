import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Eyebrow, PageHeading, SectionHeading, StatBox, FieldLabel, Chip } from "@/components/views/Display";

describe("Display Primitives", () => {
  it("renders Eyebrow with typography styling", () => {
    render(<Eyebrow>Overview</Eyebrow>);
    const el = screen.getByText("Overview");
    expect(el).toBeInTheDocument();
    // §3.3 type ramp: sentence-case micro-label, accent gold, no tracked caps.
    expect(el).toHaveClass("text-xs", "font-semibold", "text-highlight");
    expect(el).not.toHaveClass("uppercase");
  });

  it("renders PageHeading as an h1", () => {
    render(<PageHeading>System Status</PageHeading>);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("System Status");
  });

  it("renders SectionHeading as an h2", () => {
    render(<SectionHeading>Metrics Breakdown</SectionHeading>);
    const heading = screen.getByRole("heading", { level: 2 });
    expect(heading).toHaveTextContent("Metrics Breakdown");
  });

  it("renders StatBox label and tabular value", () => {
    render(<StatBox label="Total Posts" value="1,420" />);
    expect(screen.getByText("Total Posts")).toBeInTheDocument();
    expect(screen.getByText("1,420")).toHaveClass("tabular-nums");
  });

  it("renders FieldLabel and Chip elements", () => {
    render(
      <div>
        <FieldLabel htmlFor="target">Target URL</FieldLabel>
        <Chip>Active</Chip>
      </div>
    );
    expect(screen.getByText("Target URL")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });
});
