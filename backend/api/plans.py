"""Public subscription-tier catalog.

* ``GET /api/plans`` — the ordered Basic/Pro/Team/Enterprise catalog with the
  enforceable limits from :mod:`backend.core.plans`.

The endpoint is intentionally unauthenticated: it is static marketing/enforcement
metadata (no user data), and the frontend pricing page reads it so the displayed
numbers can never drift from the server-side caps.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.core.plans import plan_catalog

router = APIRouter(tags=["plans"])


class PlanLimitsOut(BaseModel):
    urls: int | None = None
    max_posts: int | None = None
    concurrent_jobs: int | None = None
    personal_accounts: int | None = None


class PlanOut(BaseModel):
    id: str
    name: str
    limits: PlanLimitsOut


@router.get("/plans", response_model=list[PlanOut], summary="List subscription tiers")
def list_plans() -> list[PlanOut]:
    """Return every tier with its server-enforced limits (``None`` = no ceiling)."""
    return [PlanOut.model_validate(entry) for entry in plan_catalog()]
