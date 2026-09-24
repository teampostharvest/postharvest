"""Subscription tiers and their enforced limits.

Tiers: Basic / Pro / Team / Enterprise.

Limits are enforced backend-side at the API layer:

* ``urls``            — max URLs accepted per scrape job (None = global cap only)
* ``max_posts``       — max posts captured per source (None = global cap only)
* ``concurrent_jobs`` — max queued+running jobs per user
* ``personal_accounts`` — max self-owned Facebook sessions per user
                      (None = unlimited)
"""
from __future__ import annotations

PLANS: tuple[str, ...] = ("basic", "pro", "team", "enterprise")

PLAN_NAMES: dict[str, str] = {
    "basic": "Basic",
    "pro": "Pro",
    "team": "Team",
    "enterprise": "Enterprise",
}

PLAN_LIMITS: dict[str, dict] = {
    "basic": {
        "urls": 5,
        "max_posts": 500,
        "concurrent_jobs": 1,
        "personal_accounts": 1,
    },
    "pro": {
        "urls": 50,
        "max_posts": 5_000,
        "concurrent_jobs": 3,
        "personal_accounts": 5,
    },
    "team": {
        "urls": 150,
        "max_posts": 100_000,
        "concurrent_jobs": 10,
        "personal_accounts": 25,
    },
    "enterprise": {
        # No per-plan ceiling — the global MAX_URLS_PER_JOB / max_posts caps
        # stay as the only bound, and support/concurrency are negotiated.
        "urls": None,
        "max_posts": None,
        "concurrent_jobs": 10,
        "personal_accounts": None,  # unlimited
    },
}


def plan_limits(plan: str | None) -> dict:
    """Return the limits dict for a plan, falling back to Basic."""
    return PLAN_LIMITS.get((plan or "").lower(), PLAN_LIMITS["basic"])


def normalize_plan(plan: str | None) -> str:
    """Return a valid plan name (falls back to ``"basic"``)."""
    candidate = (plan or "").lower()
    return candidate if candidate in PLANS else "basic"


def personal_account_cap(plan: str | None) -> int | None:
    """Max personal accounts for a plan (None = unlimited)."""
    return plan_limits(plan).get("personal_accounts")


def plan_catalog() -> list[dict]:
    """Return the ordered tier catalog as ``{id, name, limits}`` entries.

    Single source of truth for the frontend pricing / selection UI: the
    enforceable numbers come from :data:`PLAN_LIMITS` here, never from a
    hand-copied table in the client.
    """
    return [
        {"id": plan_id, "name": PLAN_NAMES[plan_id], "limits": dict(PLAN_LIMITS[plan_id])}
        for plan_id in PLANS
    ]