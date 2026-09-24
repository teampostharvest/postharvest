import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import PricingPage, { FALLBACK_LIMITS } from "@/app/(app)/pricing/page-content";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { PlanCatalog, UserProfile } from "@/lib/types";
import { PLAN_IDS } from "@/lib/types";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listPlans: vi.fn(),
    },
  };
});

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}));

const mockApi = vi.mocked(api);
const mockUseAuth = vi.mocked(useAuth);

const CATALOG: PlanCatalog = [
  { id: "basic", name: "Basic", limits: { urls: 5, max_posts: 500, concurrent_jobs: 1, personal_accounts: 1 } },
  { id: "pro", name: "Pro", limits: { urls: 50, max_posts: 5_000, concurrent_jobs: 3, personal_accounts: 5 } },
  { id: "team", name: "Team", limits: { urls: 150, max_posts: 100_000, concurrent_jobs: 10, personal_accounts: 25 } },
  { id: "enterprise", name: "Enterprise", limits: { urls: null, max_posts: null, concurrent_jobs: 10, personal_accounts: null } },
];

function authValue(overrides: Partial<ReturnType<typeof useAuth>> = {}) {
  return {
    user: null,
    profile: { plan: "basic" } as unknown as UserProfile,
    loading: false,
    getIdToken: vi.fn(async () => "id-token"),
    refreshProfile: vi.fn(async () => undefined),
    signInWithGoogle: vi.fn(async () => undefined),
    signInWithEmail: vi.fn(async () => undefined),
    signUpWithEmail: vi.fn(async () => undefined),
    signOut: vi.fn(async () => undefined),
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockApi.listPlans.mockResolvedValue(CATALOG);
});

describe("PricingPage", () => {
  it("renders the server-enforced limits for every tier", async () => {
    mockUseAuth.mockReturnValue(authValue());
    render(<PricingPage />);

    await waitFor(() => expect(mockApi.listPlans).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("heading", { name: "Team" })).toBeInTheDocument();
    expect(screen.getByText("150")).toBeInTheDocument();
    expect(screen.getByText("100,000")).toBeInTheDocument();
  });

  it("marks the current tier and offers no self-service switch", async () => {
    mockUseAuth.mockReturnValue(authValue());
    render(<PricingPage />);

    await waitFor(() => expect(mockApi.listPlans).toHaveBeenCalledTimes(1));
    expect(screen.getByText("Current plan")).toBeInTheDocument();
    // Non-current tiers surface a "Talk to sales" secondary CTA.
    expect(screen.queryByRole("button", { name: /choose /i })).not.toBeInTheDocument();
    expect(screen.queryByText("Operator-assigned")).not.toBeInTheDocument();
    expect(screen.getAllByText("Talk to sales")).toHaveLength(3);
  });

  it("shows the current plan badge on an assigned tier", async () => {
    mockUseAuth.mockReturnValue(authValue({ profile: { plan: "enterprise" } as unknown as UserProfile }));
    render(<PricingPage />);

    await waitFor(() => expect(mockApi.listPlans).toHaveBeenCalledTimes(1));
    expect(screen.getByText("Current plan")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /choose /i })).not.toBeInTheDocument();
  });
});

describe("FALLBACK_LIMITS", () => {
  it("covers every tier with the enforced limit shape", () => {
    // Guards the hand-mirror of backend/core/plans.py PLAN_LIMITS: keys and
    // shape must match or the offline fallback silently misleads. Values
    // themselves are covered backend-side (test_plans_catalog_mirrors...).
    expect(Object.keys(FALLBACK_LIMITS).sort()).toEqual([...PLAN_IDS].sort());
    for (const plan of PLAN_IDS) {
      const limits = FALLBACK_LIMITS[plan];
      for (const key of ["urls", "max_posts", "concurrent_jobs", "personal_accounts"] as const) {
        expect(
          typeof limits[key] === "number" || limits[key] === null,
          `${plan}.${key}`
        ).toBe(true);
      }
    }
  });
});
