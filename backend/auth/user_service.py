"""User provisioning — get-or-create on first Firebase login."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.logging import get_logger

logger = get_logger("auth.user_service")

#: User fields mirrored into the Redis identity cache. Keep in sync with
#: ``backend.api.auth.UserOut`` so a cache hit reconstructs a fully-populated
#: ``User`` (missing attributes would surface as nulls in ``/api/auth/me``).
_CACHE_FIELDS = (
    "id",
    "firebase_uid",
    "email",
    "display_name",
    "photo_url",
    "plan",
    "role",
    "is_active",
)


def serialize_user(user) -> dict:
    """Flatten a User row into a JSON-safe identity payload."""
    return {field: getattr(user, field) for field in _CACHE_FIELDS}


def user_from_cache(payload: dict):
    """Rebuild a detached User from a cached identity payload.

    The instance is intentionally transient (never added to a session): every
    caller only reads identity attributes, and avoiding a DB round-trip is the
    whole point of the cache.
    """
    from backend.models.user import User

    return User(**{field: payload.get(field) for field in _CACHE_FIELDS})


def get_or_create_user(db: Session, firebase_uid: str, claims: dict):
    """Find the User by firebase_uid, or create one on first login.

    OPS_EMAILS is enforced on *every* login, not just at provisioning: an
    account that already exists (e.g. it logged in before the list was
    configured) is promoted to ops on its next login. This self-heals any
    stale ``role=user`` row. The reverse is never done — logging in with an
    email that is not in OPS_EMAILS never demotes an existing operator.
    """
    from backend.core.config import get_settings
    from backend.models.user import User

    ops_emails = {e.strip().lower() for e in get_settings().ops_emails if e.strip()}

    user = db.scalar(select(User).where(User.firebase_uid == firebase_uid))
    if user is not None:
        if (
            user.email
            and user.email.lower() in ops_emails
            and user.role != "ops"
        ):
            user.role = "ops"
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(
                "Promoted existing user id=%s to ops (email in OPS_EMAILS)",
                user.id,
            )
        return user

    email = claims.get("email")
    # Emails listed in OPS_EMAILS are promoted to the ops role on first
    # login (bootstrap path); everyone else allocates with basic plan.
    role = "ops" if email and email.lower() in ops_emails else "user"

    user = User(
        firebase_uid=firebase_uid,
        email=email,
        display_name=claims.get("name") or (email or "").split("@")[0] or "User",
        photo_url=claims.get("picture"),
        plan="basic",
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(
        "Provisioned new user id=%s uid=%s role=%s plan=%s",
        user.id, firebase_uid, user.role, user.plan,
    )
    return user
