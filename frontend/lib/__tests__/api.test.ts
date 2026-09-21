import { describe, expect, it, vi, afterEach } from "vitest";
import { isFacebookUrl, safeHttpUrl, isTerminalStatus, ApiError, api } from "@/lib/api";

describe("safeHttpUrl", () => {
  it("allows standard http and https URLs", () => {
    expect(safeHttpUrl("https://facebook.com/example")).toBe("https://facebook.com/example");
    expect(safeHttpUrl("http://example.com/path?query=1")).toBe("http://example.com/path?query=1");
  });

  it("trims whitespace from URLs", () => {
    expect(safeHttpUrl("   https://facebook.com/page   ")).toBe("https://facebook.com/page");
  });

  it("rejects dangerous non-http protocols", () => {
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==")).toBeNull();
    expect(safeHttpUrl("file:///etc/passwd")).toBeNull();
    expect(safeHttpUrl("ftp://example.com/file")).toBeNull();
  });

  it("rejects invalid or empty inputs", () => {
    expect(safeHttpUrl(null)).toBeNull();
    expect(safeHttpUrl(undefined)).toBeNull();
    expect(safeHttpUrl("")).toBeNull();
    expect(safeHttpUrl("   ")).toBeNull();
    expect(safeHttpUrl("not-a-url")).toBeNull();
  });

  it("rejects URLs containing control characters", () => {
    expect(safeHttpUrl("https://example.com/\u0000path")).toBeNull();
    expect(safeHttpUrl("https://example.com/\u001fpath")).toBeNull();
    expect(safeHttpUrl("https://example.com/\u007fpath")).toBeNull();
  });
});

describe("isFacebookUrl", () => {
  it("accepts valid Facebook page and profile URLs", () => {
    const res1 = isFacebookUrl("https://www.facebook.com/Meta");
    expect(res1.valid).toBe(true);
    expect(res1.normalized).toBe("https://www.facebook.com/Meta");
    expect(res1.reason).toBeNull();

    const res2 = isFacebookUrl("https://m.facebook.com/profile.php?id=100012345678");
    expect(res2.valid).toBe(true);
    expect(res2.normalized).toBe("https://m.facebook.com/profile.php?id=100012345678");

    const res3 = isFacebookUrl("http://facebook.com/developer");
    expect(res3.valid).toBe(true);
  });

  it("strips trailing slashes during normalization", () => {
    const res = isFacebookUrl("https://www.facebook.com/PageName///");
    expect(res.valid).toBe(true);
    expect(res.normalized).toBe("https://www.facebook.com/PageName");
  });

  it("rejects non-facebook domains", () => {
    const res = isFacebookUrl("https://twitter.com/example");
    expect(res.valid).toBe(false);
    expect(res.reason).toContain("not a facebook.com address");
  });

  it("rejects empty or root-only paths", () => {
    const empty = isFacebookUrl("");
    expect(empty.valid).toBe(false);
    expect(empty.reason).toBe("Empty URL.");

    const root = isFacebookUrl("https://www.facebook.com/");
    expect(root.valid).toBe(false);
    expect(root.reason).toContain("Provide a page or profile path");
  });

  it("rejects non-http(s) schemes", () => {
    const res = isFacebookUrl("ftp://facebook.com/page");
    expect(res.valid).toBe(false);
    expect(res.reason).toContain("URL must start with http:// or https://");
  });
});

describe("isTerminalStatus", () => {
  it("identifies completed and failed as terminal states", () => {
    expect(isTerminalStatus("completed")).toBe(true);
    expect(isTerminalStatus("failed")).toBe(true);
  });

  it("identifies non-terminal and nil states as non-terminal", () => {
    expect(isTerminalStatus("queued")).toBe(false);
    expect(isTerminalStatus("running")).toBe(false);
    expect(isTerminalStatus("paused")).toBe(false);
    expect(isTerminalStatus(null)).toBe(false);
    expect(isTerminalStatus(undefined)).toBe(false);
  });
});

describe("ApiError", () => {
  it("preserves status, code, and message properties", () => {
    const err = new ApiError({
      status: 404,
      code: "job_not_found",
      message: "Job 123 does not exist",
      details: { jobId: "123" },
    });
    expect(err).toBeInstanceOf(Error);
    expect(err.name).toBe("ApiError");
    expect(err.status).toBe(404);
    expect(err.code).toBe("job_not_found");
    expect(err.message).toBe("Job 123 does not exist");
    expect(err.details).toEqual({ jobId: "123" });
  });
});

describe("plan endpoints", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches the public tier catalog", async () => {
    const catalog = [
      {
        id: "basic",
        name: "Basic",
        limits: { urls: 5, max_posts: 500, concurrent_jobs: 1, personal_accounts: 1 },
      },
      {
        id: "enterprise",
        name: "Enterprise",
        limits: { urls: null, max_posts: null, concurrent_jobs: 10, personal_accounts: null },
      },
    ];
    const fetchMock = vi.fn();
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => catalog });
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.listPlans()).resolves.toEqual(catalog);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/api/plans");
    expect(init).toMatchObject({ cache: "no-store" });
  });

  it("fetches the quota readout", async () => {
    const usage = {
      plan: "pro",
      jobs_running: { used: 2, limit: 3 },
      personal_accounts: { used: 1, limit: 5 },
      per_job: { urls: 50, max_posts: 5000 },
      posts_today: 128,
    };
    const fetchMock = vi.fn();
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => usage });
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.getUsage()).resolves.toEqual(usage);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/api/usage");
    expect(init).toMatchObject({ cache: "no-store" });
  });

  it("passes the status filter when listing jobs", async () => {
    const page = { items: [], total: 0, page: 1, page_size: 25 };
    const fetchMock = vi.fn();
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => page });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      api.listJobs({ status: "queued,running", page: 1, page_size: 25 })
    ).resolves.toEqual(page);

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/api/jobs?");
    expect(String(url)).toContain("status=queued%2Crunning");
  });

  it("bounds every request with an abort signal", async () => {
    const usage = {
      plan: "basic",
      jobs_running: { used: 0, limit: 1 },
      personal_accounts: { used: 0, limit: 1 },
      per_job: { urls: 5, max_posts: 500 },
      posts_today: 0,
      jobs_today: 0,
    };
    const fetchMock = vi.fn();
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => usage });
    vi.stubGlobal("fetch", fetchMock);

    await api.getUsage();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init?.signal).toBeInstanceOf(AbortSignal);
  });
});
