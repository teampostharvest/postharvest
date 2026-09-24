from __future__ import annotations

import glob
import os
import firebase_admin
from firebase_admin import auth as firebase_auth, credentials

from backend.core.config import get_settings
from backend.core.exceptions import AppError
from backend.core.logging import get_logger

logger = get_logger("auth.firebase")

_app: firebase_admin.App | None = None


def _init_firebase() -> firebase_admin.App:
    """Initialize Firebase Admin SDK using service account credentials or env vars."""
    global _app
    if _app is not None:
        return _app

    if firebase_admin._apps:
        _app = firebase_admin.get_app()
        return _app

    settings = get_settings()

    # 1. Try explicit env vars (private key / client email / project id)
    if settings.firebase_client_email and settings.firebase_private_key:
        private_key = settings.firebase_private_key.replace("\\n", "\n")
        if "BEGIN PRIVATE KEY" not in private_key:
            private_key = f"-----BEGIN PRIVATE KEY-----\n{private_key.strip()}\n-----END PRIVATE KEY-----\n"
        try:
            cred = credentials.Certificate({
                "type": "service_account",
                "project_id": settings.firebase_project_id or "postharvest-5a5bb",
                "client_email": settings.firebase_client_email,
                "private_key": private_key,
                "token_uri": "https://oauth2.googleapis.com/token",
            })
            _app = firebase_admin.initialize_app(cred)
            logger.info("Firebase Admin initialized via environment variable credentials")
            return _app
        except Exception as exc:
            logger.warning("Failed to initialize Firebase Admin via env vars, attempting file fallback: %s", exc)

    # 2. Try JSON file path in config or search workspace for *-firebase-adminsdk-*.json
    json_path = settings.firebase_credentials_path
    if not json_path:
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        matches = glob.glob(os.path.join(root_dir, "*firebase-adminsdk*.json"))
        if matches:
            json_path = matches[0]

    if json_path and os.path.isfile(json_path):
        cred = credentials.Certificate(json_path)
        _app = firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin initialized via certificate file: %s", os.path.basename(json_path))
        return _app

    # 3. Fallback to default application credentials if available
    try:
        cred = credentials.ApplicationDefault()
        _app = firebase_admin.initialize_app(cred, {"projectId": settings.firebase_project_id or "postharvest-firebase"})
        logger.info("Firebase Admin initialized via Application Default Credentials")
        return _app
    except Exception as exc:
        logger.warning("Firebase Admin SDK failed to initialize: %s", exc)
        raise AppError(
            "Firebase authentication is not configured on the backend.",
            status_code=503,
            code="firebase_unavailable",
        ) from exc


def verify_id_token(token: str) -> dict:
    """Verify a Firebase ID token. Returns the decoded claims dict.

    Raises AppError(401) on any failure.
    """
    _init_firebase()
    try:
        decoded = firebase_auth.verify_id_token(token)
        return decoded
    except firebase_auth.ExpiredIdTokenError:
        raise AppError("Token expired", status_code=401, code="token_expired")
    except firebase_auth.RevokedIdTokenError:
        raise AppError("Token revoked", status_code=401, code="token_revoked")
    except firebase_auth.InvalidIdTokenError:
        logger.warning("Invalid Firebase ID token presented")
        raise AppError("Invalid token", status_code=401, code="invalid_token")
    except Exception as exc:
        if isinstance(exc, AppError):
            raise
        logger.exception("Firebase token verification failed")
        raise AppError(
            "Authentication failed",
            status_code=401,
            code="invalid_token",
        ) from exc
