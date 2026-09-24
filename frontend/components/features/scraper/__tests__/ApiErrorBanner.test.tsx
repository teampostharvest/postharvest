import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiErrorBanner } from "@/components/features/scraper/ApiErrorBanner";

describe("ApiErrorBanner", () => {
  it("renders title and message correctly", () => {
    render(
      <ApiErrorBanner
        title="Extraction Failed"
        message="Facebook blocked the request due to throttling."
      />
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Extraction Failed")).toBeInTheDocument();
    expect(screen.getByText("Facebook blocked the request due to throttling.")).toBeInTheDocument();
  });

  it("renders retry button and triggers callback on click", () => {
    const onRetry = vi.fn();
    render(
      <ApiErrorBanner
        title="Network Error"
        message="Connection reset"
        onRetry={onRetry}
        retryLabel="Retry Job"
      />
    );
    const retryBtn = screen.getByRole("button", { name: /retry job/i });
    expect(retryBtn).toBeInTheDocument();
    fireEvent.click(retryBtn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("applies warning variant styles when requested", () => {
    render(
      <ApiErrorBanner
        title="Rate Limit Warning"
        message="Slowing down request throughput"
        variant="warning"
      />
    );
    expect(screen.getByRole("alert")).toHaveClass("border-dashed");
  });
});
