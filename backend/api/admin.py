"""Operator-only admin endpoints (ops role).

* GET    /api/admin/users           — list all users (id, email, plan, role, …)
* PATCH  /api/admin/users/{id}/role — promote/demote between user/ops
* PATCH  /api/admin/users/{id}/plan — set Basic/Pro/Team/Enterprise tier

Every route is gated by :func:`require_ops` (403 ``admin_required`` for
regular users). An operator cannot demote themselves, to avoid accidentally
locking the last admin out.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth.dependencies import require_ops
from backend.core import cache
from backend.core.database import get_db
from backend.core.exceptions import AppError, NotFoundError
from backend.core.plans import PLANS, normalize_plan
from backend.models.user import User

router = APIRouter(tags=["admin"])


ROLES = ("user", "ops")


class AdminUserOut(BaseModel):
    id: int
    email: str | None = None
    display_name: str | None = None
    plan: str
    role: str
    is_active: bool
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class RoleUpdate(BaseModel):
    role: Literal["user", "ops"] = Field(..., description="New role")


class PlanUpdate(BaseModel):
    plan: Literal["basic", "pro", "team", "enterprise"] = Field(..., description="New tier")


@router.get(
    "/admin/users",
    response_model=list[AdminUserOut],
    summary="List all users (ops only)",
)
def list_users(
    db: Session = Depends(get_db),
    _ops: User = Depends(require_ops),
) -> list[AdminUserOut]:
    users = db.scalars(select(User).order_by(User.created_at.asc(), User.id.asc())).all()
    return [AdminUserOut.model_validate(u) for u in users]


@router.patch(
    "/admin/users/{user_id}/role",
    response_model=AdminUserOut,
    summary="Change a user's role (ops only)",
)
def set_user_role(
    user_id: int,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    current_ops: User = Depends(require_ops),
) -> AdminUserOut:
    user = _get_user_or_404(db, user_id)
    if user.id == current_ops.id and payload.role != "ops":
        raise AppError(
            "You cannot demote yourself",
            status_code=400,
            code="invalid_role_change",
        )
    user.role = payload.role
    db.commit()
    db.refresh(user)
    # The identity cache gates ops-only routes; a stale role must not outlive
    # the admin action that changed it.
    cache.invalidate_user(user.firebase_uid)
    return AdminUserOut.model_validate(user)


@router.patch(
    "/admin/users/{user_id}/plan",
    response_model=AdminUserOut,
    summary="Change a user's subscription tier (ops only)",
)
def set_user_plan(
    user_id: int,
    payload: PlanUpdate,
    db: Session = Depends(get_db),
    _ops: User = Depends(require_ops),
) -> AdminUserOut:
    user = _get_user_or_404(db, user_id)
    user.plan = normalize_plan(payload.plan)
    db.commit()
    db.refresh(user)
    # Plan drives quota enforcement and the usage readout; drop the cached
    # identity so the new tier takes effect on the next request.
    cache.invalidate_user(user.firebase_uid)
    return AdminUserOut.model_validate(user)


def _get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError(f"User {user_id} not found")
    return user