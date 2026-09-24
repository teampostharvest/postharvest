import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { UrlInputCard } from "@/components/features/scraper/UrlInputCard";
import { readActiveAccount, writeActiveAccount } from "@/lib/settings";
import type { ScrapeRequest } from "@/lib/types";

vi.mock("@/lib/settings", () => ({
  readScrapeDefaults: vi.fn(() => ({
    maxPosts: "",
    postType: "" as const,
    scrolls: "",
    useBrowser: false,
  })),
  readActiveAccount: vi.fn(() => null),
  writeActiveAccount: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listAccounts: vi.fn(async () => ({
        ops: [
          { name: "ops-pool-1", scope: "ops" as const, status: "VALID" as const },
          { name: "ops-expired", scope: "ops" as const, status: "EXPIRED" as const },
        ],
        mine: [{ name: "personal-1", scope: "me" as const }],
      })),
    },
  };
});

const URLS_INPUT = /facebook\.com\/examplepage/;
const VALID_URL = "https://www.facebook.com/ExamplePage";

describe("UrlInputCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the form with submit disabled until a valid URL is present", () => {
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} />);
    expect(screen.getByText("Targets")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start scraping/i })).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
  });

  it("validates one valid URL and submits a normalized ScrapeRequest", () => {
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} />);

    fireEvent.change(screen.getByPlaceholderText(URLS_INPUT), {
      target: { value: VALID_URL },
    });
    expect(screen.getByText("1 valid")).toBeInTheDocument();

    const startBtn = screen.getByRole("button", { name: /start scraping/i });
    expect(startBtn).toBeEnabled();
    fireEvent.click(startBtn);

    const payload = submit.mock.calls[0][0] as ScrapeRequest;
    expect(payload.urls).toEqual(["https://www.facebook.com/ExamplePage"]);
    expect(payload.use_browser).toBe(false);
    expect(payload.account).toBeNull();
    expect(payload.scrolls).toBeNull();
    expect(payload.max_posts).toBeNull();
    expect(payload.post_type).toBeNull();
    expect(payload.start_date).toBeNull();
    expect(payload.end_date).toBeNull();
  });

  it("flags invalid URLs with a reason and keeps submit locked", () => {
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} />);

    fireEvent.change(screen.getByPlaceholderText(URLS_INPUT), {
      target: { value: "https://twitter.com/not-facebook" },
    });
    expect(screen.getByText("1 invalid")).toBeInTheDocument();
    expect(screen.getByText(/not a facebook\.com address/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start scraping/i })).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
  });

  it("deduplicates repeated lines and counts the ignored duplicates", () => {
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} />);

    const duplicated = `${VALID_URL}\n${VALID_URL}\nhttps://twitter.com/x`;
    fireEvent.change(screen.getByPlaceholderText(URLS_INPUT), {
      target: { value: duplicated },
    });

    expect(screen.getByText("1 valid")).toBeInTheDocument();
    expect(screen.getByText("1 invalid")).toBeInTheDocument();
    expect(screen.getByText(/1 duplicate line ignored/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /start scraping/i }));
    const payload = submit.mock.calls[0][0] as ScrapeRequest;
    expect(payload.urls).toEqual(["https://www.facebook.com/ExamplePage"]);
  });

  it("sends max_posts and post_type when provided", () => {
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} />);

    fireEvent.change(screen.getByPlaceholderText(URLS_INPUT), {
      target: { value: VALID_URL },
    });
    fireEvent.change(screen.getByLabelText("Maximum posts"), { target: { value: "25" } });
    fireEvent.change(screen.getByLabelText("Post type"), { target: { value: "video" } });
    fireEvent.click(screen.getByRole("button", { name: /start scraping/i }));

    const payload = submit.mock.calls[0][0] as ScrapeRequest;
    expect(payload.max_posts).toBe(25);
    expect(payload.post_type).toBe("video");
  });

  it("applies a time frame preset to start/end dates", () => {
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} />);

    fireEvent.change(screen.getByPlaceholderText(URLS_INPUT), {
      target: { value: VALID_URL },
    });
    fireEvent.click(screen.getByRole("button", { name: "Last 30 days" }));
    fireEvent.click(screen.getByRole("button", { name: /start scraping/i }));

    const payload = submit.mock.calls[0][0] as ScrapeRequest;
    expect(payload.start_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(payload.end_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("blocks submission when a custom date range is inverted", () => {
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} />);

    fireEvent.change(screen.getByPlaceholderText(URLS_INPUT), {
      target: { value: VALID_URL },
    });
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-09-20" } });
    fireEvent.change(screen.getByLabelText("End date"), { target: { value: "2026-09-01" } });

    expect(screen.getByText(/End date must be on or after start date/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start scraping/i })).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
  });

  it("reveals session controls in browser mode and sends account + scrolls", async () => {
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} />);

    fireEvent.change(screen.getByPlaceholderText(URLS_INPUT), {
      target: { value: VALID_URL },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /browser mode/i }));

    const accountSelect = await screen.findByLabelText(/saved account/i);
    const scrollSelect = screen.getByLabelText(/scroll rounds/i);

    expect(accountSelect).toBeInTheDocument();
    expect(scrollSelect).toBeInTheDocument();

    // Accounts load asynchronously; wait for the option before selecting it.
    // Changing an empty <select> is clamped back to "" by jsdom, which drops
    // the account from the payload and flakes this test under load.
    await screen.findByRole("option", { name: /ops-pool-1/ });

    fireEvent.change(accountSelect, { target: { value: "ops:ops-pool-1" } });
    fireEvent.change(scrollSelect, { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: /start scraping/i }));

    const payload = submit.mock.calls[0][0] as ScrapeRequest;
    expect(payload.use_browser).toBe(true);
    expect(payload.account).toBe("ops:ops-pool-1");
    expect(payload.scrolls).toBe(20);
    // The session is remembered as the panel's active account.
    expect(writeActiveAccount).toHaveBeenCalledWith("ops:ops-pool-1");
  });

  it("prefills the account dropdown from the active session when it still exists", async () => {
    vi.mocked(readActiveAccount).mockReturnValue("ops:ops-pool-1");
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /browser mode/i }));
    const accountSelect = (await screen.findByLabelText(/saved account/i)) as HTMLSelectElement;
    await screen.findByRole("option", { name: /ops-pool-1/ });
    expect(accountSelect.value).toBe("ops:ops-pool-1");
  });

  it("prefills from initialUrls prop", () => {
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} initialUrls={VALID_URL} />);
    expect(screen.getByText("1 valid")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start scraping/i })).toBeEnabled();
  });

  it("clears the whole form with the Clear button", () => {
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} />);

    fireEvent.change(screen.getByPlaceholderText(URLS_INPUT), {
      target: { value: VALID_URL },
    });
    expect(screen.getByText("1 valid")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /clear/i }));
    expect(screen.queryByText("1 valid")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start scraping/i })).toBeDisabled();
  });

  it("locks all inputs while a job is running", () => {
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} disabled />);

    expect(screen.getByPlaceholderText(URLS_INPUT)).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /browser mode/i })).toBeDisabled();
    expect(screen.getByText(/inputs are locked/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start scraping/i })).toBeDisabled();
  });

  it("shows the submitting label while a scrape is starting", () => {
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} submitting />);
    expect(screen.getByText("Starting…")).toBeInTheDocument();
  });

  it("notes that saved sessions apply to browser runs only", async () => {
    const submit = vi.fn();
    render(<UrlInputCard onSubmit={submit} />);
    // Accounts load async; the note appears once sessions exist.
    await screen.findByText("Saved sessions apply to browser-mode runs only.");
    fireEvent.click(screen.getByRole("checkbox", { name: /browser mode/i }));
    expect(screen.queryByText("Saved sessions apply to browser-mode runs only.")).not.toBeInTheDocument();
  });
});