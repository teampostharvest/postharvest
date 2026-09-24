import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";

describe("Card", () => {
  it("renders a styled container with children", () => {
    render(<Card data-testid="card">content</Card>);
    expect(screen.getByTestId("card")).toHaveTextContent("content");
    expect(screen.getByTestId("card").className).toContain("rounded-lg");
  });

  it("merges custom className with defaults", () => {
    render(<Card data-testid="card" className="bg-red-500" />);
    const card = screen.getByTestId("card");
    expect(card.className).toContain("bg-red-500");
    expect(card.className).toContain("rounded-lg");
  });

  it("renders header, title, description, content and footer", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Job summary</CardTitle>
          <CardDescription>Overview of the scrape run</CardDescription>
        </CardHeader>
        <CardContent>3 sources</CardContent>
        <CardFooter>View details</CardFooter>
      </Card>
    );
    expect(screen.getByText("Job summary")).toBeInTheDocument();
    expect(screen.getByText("Overview of the scrape run")).toBeInTheDocument();
    expect(screen.getByText("3 sources")).toBeInTheDocument();
    expect(screen.getByText("View details")).toBeInTheDocument();
  });

  it("renders CardTitle as a heading element", () => {
    render(<CardTitle>Heading</CardTitle>);
    const title = screen.getByText("Heading");
    expect(title.tagName).toBe("H3");
    expect(title.className).toContain("font-semibold");
  });

  it("forwards data attributes to composed sections", () => {
    render(<CardContent data-testid="body">body</CardContent>);
    expect(screen.getByTestId("body")).toHaveTextContent("body");
  });
});