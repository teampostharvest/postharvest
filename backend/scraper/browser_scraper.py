"""Playwright-based browser scraper for Facebook pages.

Uses a real headless browser to execute JavaScript, scroll the page,
and extract posts that are dynamically loaded via infinite scroll.

Supports authenticated scraping via saved cookies (from cli.py login).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from bs4 import BeautifulSoup
from backend.scraper.parser import find_post_roots

logger = logging.getLogger("scraper.browser")

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
COOKIES_PATH = DATA_DIR / "fb_cookies.json"
CREDENTIALS_PATH = DATA_DIR / "fb_credentials.json"

MAX_SCROLL_ROUNDS = 40
SCROLL_DELAY = 1.5


def _has_browser() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _base_data_dir() -> Path:
    """The runtime data dir — resolved from settings so DATA_DIR env is honored.

    (Path resolution happens at call time, not import time, so tests and
    deployments that override DATA_DIR get the right store.)
    """
    from backend.core.config import get_settings

    return Path(get_settings().data_dir)


# ---------------------------------------------------------------------------
# Owner/scope-aware cookie store
#
# Two tiers (locked decision, 2026-09-17):
# * ops pool  — global store under ``data/`` (unchanged layout); owner_id=None
# * personal  — per-user store under ``data/personal/{owner_id}/``; cookies are
#   encrypted at rest when ``cookie_encryption_key`` is configured.
# ---------------------------------------------------------------------------


def _cipher():
    """Return a Fernet cipher for personal at-rest encryption, or None."""
    from backend.core.config import get_settings

    key = get_settings().cookie_encryption_key
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key if isinstance(key, bytes) else key.encode("utf-8"))
    except Exception:  # pragma: no cover - misconfigured key
        logger.warning(
            "cookie_encryption_key is set but Fernet is unavailable; "
            "personal cookies will be stored in plain JSON"
        )
        return None


def _personal_dir(owner_id: int | None) -> Path:
    """Per-user cookie store directory for the given owner id."""
    if owner_id is None:
        raise ValueError("owner_id is required for the personal cookie store")
    return _base_data_dir() / "personal" / str(owner_id)


def _credentials_path(owner_id: int | None = None) -> Path:
    """Path to the credentials index for a scope (ops when owner_id is None)."""
    if owner_id is None:
        return _base_data_dir() / "fb_credentials.json"
    return _personal_dir(owner_id) / "fb_credentials.json"


def _cookies_path(account_name: str | None, owner_id: int | None = None) -> Path:
    """Resolve the cookies file path for an account within a scope."""
    base = _base_data_dir()
    if owner_id is not None:
        return _personal_dir(owner_id) / (
            f"fb_cookies_{account_name}.json" if account_name else "fb_cookies.json"
        )
    if account_name and account_name != "default":
        return base / f"fb_cookies_{account_name}.json"
    return base / "fb_cookies.json"


def _mirror_save(payload_text: str, account_name: str | None, owner_id: int | None) -> None:
    """Upsert the ``saved_accounts`` mirror row (best-effort).

    Disk files remain authoritative — a DB failure must never block or fail
    the save, so any exception is logged and swallowed.
    """
    try:
        from backend.core.database import get_session_context
        from backend.models.saved_account import SavedAccount

        scope = "me" if owner_id is not None else "ops"
        name = (account_name or "default").strip() or "default"
        now = datetime.now(timezone.utc)
        with get_session_context() as db:
            row = (
                db.query(SavedAccount)
                .filter_by(scope=scope, owner_id=owner_id, name=name)
                .first()
            )
            if row is None:
                db.add(
                    SavedAccount(
                        scope=scope,
                        owner_id=owner_id,
                        name=name,
                        cookies=payload_text,
                        meta={"saved_at": now.isoformat()},
                    )
                )
            else:
                row.cookies = payload_text
                row.meta = {**(row.meta or {}), "saved_at": now.isoformat()}
                row.updated_at = now
    except Exception as exc:  # noqa: BLE001 - mirror is best-effort
        logger.warning(
            "saved_accounts mirror upsert failed (%s/%s): %s",
            "me" if owner_id is not None else "ops",
            account_name or "default",
            exc,
        )


def _load_from_db(account_name: str | None, owner_id: int | None) -> Optional[list]:
    """Fallback read of a jar from the saved_accounts mirror table.

    Used when the on-disk file is missing (e.g. a restore onto a fresh host
    that has the mirror but not the files). Returns None when there is no row
    or the payload cannot be parsed/decrypted.
    """
    try:
        from backend.core.database import get_session_context
        from backend.models.saved_account import SavedAccount

        scope = "me" if owner_id is not None else "ops"
        name = (account_name or "default").strip() or "default"
        with get_session_context() as db:
            row = (
                db.query(SavedAccount)
                .filter_by(scope=scope, owner_id=owner_id, name=name)
                .first()
            )
            if row is None:
                return None
            raw = row.cookies
    except Exception as exc:  # noqa: BLE001 - best-effort fallback
        logger.warning("saved_accounts mirror read failed: %s", exc)
        return None

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("__encrypted__"):
            cipher = _cipher()
            if cipher is None:
                logger.error(
                    "Mirror row is encrypted but no cookie_encryption_key is "
                    "configured; cannot load %s/%s",
                    "me" if owner_id is not None else "ops",
                    account_name or "default",
                )
                return None
            data = json.loads(cipher.decrypt(data["payload"].encode("ascii")).decode("utf-8"))
        if data:
            logger.info("Loaded %d cookies from saved_accounts mirror", len(data))
        return data if data else None
    except Exception:
        return None


def _mirror_delete(account_name: str, owner_id: int | None) -> None:
    """Remove the saved_accounts mirror row (best-effort)."""
    try:
        from backend.core.database import get_session_context
        from backend.models.saved_account import SavedAccount

        scope = "me" if owner_id is not None else "ops"
        name = (account_name or "").strip() or "default"
        with get_session_context() as db:
            (
                db.query(SavedAccount)
                .filter_by(scope=scope, owner_id=owner_id, name=name)
                .delete()
            )
    except Exception as exc:  # noqa: BLE001 - best-effort
        logger.warning("saved_accounts mirror delete failed: %s", exc)


def _db_extra_metadata(owner_id: int | None, disk_names: set[str]) -> list[dict]:
    """Mirror-only rows (e.g. boot-imported jars whose files are gone).

    Returns metadata for saved_accounts rows that have no on-disk counterpart
    so a restored host still lists its sessions. Never cookie contents.
    """
    try:
        from backend.core.database import get_session_context
        from backend.models.saved_account import SavedAccount

        scope = "me" if owner_id is not None else "ops"
        with get_session_context() as db:
            rows = db.query(SavedAccount).filter_by(scope=scope, owner_id=owner_id).all()
    except Exception as exc:  # noqa: BLE001 - best-effort
        logger.warning("saved_accounts metadata scan failed: %s", exc)
        return []

    extra = []
    for row in rows:
        if row.name in disk_names:
            continue
        saved_at = None
        if isinstance(row.meta, dict):
            saved_at = row.meta.get("saved_at")
        extra.append(
            {
                "name": row.name,
                "cookies_file": None,
                "saved_at": saved_at,
                "scope": scope,
            }
        )
    return extra


def parse_account_spec(spec: str | None) -> tuple[str, str | None]:
    """Split a user-supplied account reference into ``(scope, name)``.

    Accepted forms:
        ``"ops:<name>"`` / ``"me:<name>"``  -> explicit scope
        ``"<name>"``                        -> ops pool (backwards compatible)

    The empty string / None resolves to the ops ``default`` session.
    """
    if not spec:
        return "ops", None
    if ":" in spec:
        possible_scope, _, rest = spec.partition(":")
        if possible_scope in ("ops", "me") and rest:
            return possible_scope, rest.strip() or None
    return "ops", spec.strip() or None


def _serialize_payload(cookies: list, owner_id: int | None) -> Any:
    """Serialize a jar the same way it is written to disk.

    Personal jars are Fernet-encrypted at rest when a cookie_encryption_key is
    configured (mirroring the file format); ops-pool jars stay plain.
    """
    payload: Any = cookies
    cipher = _cipher() if owner_id is not None else None
    if cipher is not None:
        raw = json.dumps(cookies, ensure_ascii=False).encode("utf-8")
        payload = {"__encrypted__": True, "payload": cipher.encrypt(raw).decode("ascii")}
    return payload


def save_cookies(
    cookies: list,
    account_name: str | None = None,
    owner_id: int | None = None,
) -> None:
    """Persist browser cookies to disk for a scope.

    ``owner_id=None`` targets the global ops store; otherwise cookies are
    saved under ``data/personal/{owner_id}/`` and encrypted at rest when a
    ``cookie_encryption_key`` is configured. The jar is then mirrored into the
    ``saved_accounts`` table (disk stays authoritative; mirror failures only
    log a warning).
    """
    data_dir = _base_data_dir() if owner_id is None else _personal_dir(owner_id)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _cookies_path(account_name, owner_id)

    payload = _serialize_payload(cookies, owner_id)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    logger.info(
        "Saved %d cookies to %s (owner_id=%s)", len(cookies), path, owner_id
    )

    if account_name:
        _update_credentials_index(account_name, path, owner_id=owner_id)

    _mirror_save(json.dumps(payload, ensure_ascii=False), account_name, owner_id)


def _update_credentials_index(
    account_name: str,
    cookies_path: Path,
    owner_id: int | None = None,
) -> None:
    """Add/update an account in the credentials index for a scope."""
    index = load_credentials(owner_id)
    index[account_name] = {
        "cookies_file": str(cookies_path.name),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "encrypted": owner_id is not None and _cipher() is not None,
    }
    index_path = _credentials_path(owner_id)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    logger.info("Updated credentials index: %s (owner_id=%s)", account_name, owner_id)


def load_credentials(owner_id: int | None = None) -> dict:
    """Load the credentials index for a scope.

    Returns ``{account_name: {cookies_file, saved_at}}`` (ops scope) or the
    per-user index (personal scope).
    """
    path = _credentials_path(owner_id)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def account_metadata(owner_id: int | None = None) -> list[dict]:
    """Metadata for all saved accounts in a scope (never cookie contents).

    Each item: ``{name, cookies_file, saved_at, scope}``. The implicit ops
    ``default`` session (plain ``fb_cookies.json`` without an index entry)
    is folded in for the ops scope.
    """
    creds = load_credentials(owner_id)
    scope = "me" if owner_id is not None else "ops"
    items = [
        {
            "name": name,
            "cookies_file": entry.get("cookies_file") if isinstance(entry, dict) else None,
            "saved_at": entry.get("saved_at") if isinstance(entry, dict) else None,
            "scope": scope,
        }
        for name, entry in creds.items()
    ]
    if owner_id is None and (_base_data_dir() / "fb_cookies.json").exists() and "default" not in creds:
        items.append(
            {
                "name": "default",
                "cookies_file": "fb_cookies.json",
                "saved_at": None,
                "scope": "ops",
            }
        )
    # Fold in mirror-only rows (boot-imported jars that have no disk file yet).
    items.extend(_db_extra_metadata(owner_id, {item["name"] for item in items}))
    items.sort(key=lambda item: item["name"])
    return items


def list_accounts(owner_id: int | None = None) -> list[str]:
    """Return names of all saved accounts in a scope."""
    return [item["name"] for item in account_metadata(owner_id)]


def get_cookie_status(account_name: str | None = None, owner_id: int | None = None) -> str:
    """Return ``"VALID"`` or ``"EXPIRED"`` for the given account's cookies.

    Checks the ``xs`` (session) cookie expiry against the current time.
    Returns ``"EXPIRED"`` when the cookie file is missing, unreadable,
    or the ``xs`` cookie has expired.
    """
    import time as _time

    cookies = load_cookies(account_name, owner_id=owner_id)
    if not cookies:
        return "EXPIRED"
    now = _time.time()
    xs = next((c for c in cookies if c.get("name") == "xs"), None)
    if xs is None:
        return "EXPIRED"
    expires = xs.get("expires")
    if expires is None or expires < now:
        return "EXPIRED"
    return "VALID"


def load_cookies(account_name: str | None = None, owner_id: int | None = None) -> Optional[list]:
    """Load saved cookies for an account within a scope, or None if missing.

    ``owner_id=None`` reads the global ops store; otherwise the per-user
    store (transparently decrypting at-rest encrypted files).
    """
    if owner_id is not None:
        creds = load_credentials(owner_id)
        if account_name:
            if account_name not in creds:
                if account_name != "default":
                    logger.warning(
                        "Account '%s' not found in personal credentials (owner_id=%s)",
                        account_name, owner_id,
                    )
                    return None
                path = _personal_dir(owner_id) / "fb_cookies.json"
            else:
                path = _personal_dir(owner_id) / creds[account_name]["cookies_file"]
        else:
            path = _personal_dir(owner_id) / "fb_cookies.json"
    elif account_name:
        creds = load_credentials()
        if account_name not in creds:
            # A login without --account is stored under the plain default
            # file rather than the credentials index; fall back to it so
            # account_name="default" still unlocks the session.
            if account_name == "default":
                path = _base_data_dir() / "fb_cookies.json"
            else:
                logger.warning("Account '%s' not found in credentials", account_name)
                return None
        else:
            path = _base_data_dir() / creds[account_name]["cookies_file"]
    else:
        path = _base_data_dir() / "fb_cookies.json"

    if not path.exists():
        return _load_from_db(account_name, owner_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("__encrypted__"):
            cipher = _cipher()
            if cipher is None:
                logger.error(
                    "Personal cookies are encrypted but no cookie_encryption_key "
                    "is configured; cannot load %s", path,
                )
                return None
            raw = cipher.decrypt(data["payload"].encode("ascii"))
            data = json.loads(raw.decode("utf-8"))
        if data:
            logger.info("Loaded %d cookies from %s", len(data), path)
        return data if data else None
    except Exception:
        return None


def delete_account(account_name: str, owner_id: int | None = None) -> bool:
    """Remove an account's cookies file and index entry for a scope.

    Returns True if anything was removed, False when the account is unknown.
    """
    name = (account_name or "").strip()
    if not name:
        return False

    creds = load_credentials(owner_id)
    if name in creds:
        entry = creds[name]
        cookies_file = entry.get("cookies_file") if isinstance(entry, dict) else None
        base = _personal_dir(owner_id) if owner_id is not None else _base_data_dir()
        if cookies_file:
            (base / cookies_file).unlink(missing_ok=True)
        del creds[name]
        index_path = _credentials_path(owner_id)
        if creds:
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(creds, f, indent=2, ensure_ascii=False)
        else:
            index_path.unlink(missing_ok=True)
        _mirror_delete(name, owner_id)
        return True

    if name == "default":  # implicit default session (plain file, no index entry)
        path = _cookies_path("default", owner_id)
        if path.exists():
            path.unlink(missing_ok=True)
            _mirror_delete(name, owner_id)
            return True
    return False


def import_cookie_files_to_db() -> int:
    """Backfill the ``saved_accounts`` mirror from on-disk jars (one-time).

    Called from ``init_db()`` at boot. Scans the ops store (``data/``), every
    personal store (``data/personal/{owner_id}/``) and the credentials
    indexes, inserting a mirror row per jar. No-op once the table has rows —
    disk remains authoritative. Returns the number of rows inserted.
    """
    from backend.core.database import get_session_context
    from backend.models.saved_account import SavedAccount

    data_dir = _base_data_dir()
    inserted = 0

    def _insert(scope: str, owner_id: int | None, name: str, payload_text: str, saved_at=None) -> None:
        nonlocal inserted
        existing = (
            db.query(SavedAccount)
            .filter_by(scope=scope, owner_id=owner_id, name=name)
            .first()
        )
        if existing is not None:
            return
        db.add(
            SavedAccount(
                scope=scope,
                owner_id=owner_id,
                name=name,
                cookies=payload_text,
                meta={"imported": True, "saved_at": saved_at},
            )
        )
        inserted += 1

    try:
        with get_session_context() as db:
            if db.query(SavedAccount).first() is not None:
                return 0

            # --- ops pool (data/) ---
            creds = load_credentials()
            for name, entry in creds.items():
                fname = entry.get("cookies_file") if isinstance(entry, dict) else None
                if fname and (data_dir / fname).exists():
                    _insert(
                        "ops", None, name,
                        (data_dir / fname).read_text(encoding="utf-8"),
                        entry.get("saved_at") if isinstance(entry, dict) else None,
                    )
            implicit = data_dir / "fb_cookies.json"
            if implicit.exists() and "default" not in creds:
                _insert("ops", None, "default", implicit.read_text(encoding="utf-8"))

            # --- personal stores (data/personal/{owner_id}/) ---
            personal_root = data_dir / "personal"
            if personal_root.is_dir():
                for owner_dir in sorted(personal_root.iterdir()):
                    try:
                        owner_id = int(owner_dir.name)
                    except ValueError:
                        continue
                    ocreds = load_credentials(owner_id=owner_id)
                    for name, entry in ocreds.items():
                        fname = entry.get("cookies_file") if isinstance(entry, dict) else None
                        if fname and (owner_dir / fname).exists():
                            _insert(
                                "me", owner_id, name,
                                (owner_dir / fname).read_text(encoding="utf-8"),
                                entry.get("saved_at") if isinstance(entry, dict) else None,
                            )
                    implicit_p = owner_dir / "fb_cookies.json"
                    if implicit_p.exists() and "default" not in ocreds:
                        _insert("me", owner_id, "default", implicit_p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - import must never break boot
        logger.warning("cookie file import skipped: %s", exc)
        return inserted

    if inserted:
        logger.info("Mirrored %d on-disk cookie jar(s) into saved_accounts", inserted)
    return inserted


def login_with_browser(timeout_seconds: int = 120, account_name: str | None = None) -> bool:
    """Open a visible browser for the user to log into Facebook.

    Polls for Facebook session cookies to appear (indicating successful login).
    Returns True if cookies were saved successfully.

    :param timeout_seconds: how long to wait for login (default 120s).
    :param account_name: optional label to save cookies under a specific name.
    """
    from playwright.sync_api import sync_playwright

    label = f" for account '{account_name}'" if account_name else ""
    print(f"\nA browser window will open. Please log into Facebook{label}.")
    print(f"Waiting up to {timeout_seconds}s for login to complete...\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = context.new_page()
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=30000)

        # Poll for login: check every 3s for facebook session cookies
        deadline = time.monotonic() + timeout_seconds
        logged_in = False
        while time.monotonic() < deadline:
            cookies = context.cookies()
            fb_cookies = [c for c in cookies if "facebook.com" in c.get("domain", "")]
            # c_user is the main Facebook session indicator
            has_session = any(c["name"] == "c_user" for c in fb_cookies)
            if has_session:
                logged_in = True
                print("Login detected!")
                break
            time.sleep(3)

        cookies = context.cookies()
        browser.close()

    if logged_in and cookies:
        save_cookies(cookies, account_name=account_name)
        fb_cookies = [c for c in cookies if "facebook.com" in c.get("domain", "")]
        print(f"Login successful! Saved {len(fb_cookies)} Facebook cookies.")
        if account_name:
            print(f"Account saved as '{account_name}'. Use --account {account_name} to scrape with it.")
        return True
    else:
        print("Login not detected. Please try again.")
        return False


def login_with_credentials(
    email: str,
    password: str,
    owner_id: int | None = None,
    account_name: str | None = None,
    timeout_seconds: float | None = None,
) -> bool:
    """Headless Facebook login for personal cookies (server-side flow).

    Fills the Facebook email/password form in a headless browser and waits
    for the ``c_user`` session cookie to appear, then saves the captured
    cookies under ``data/personal/{owner_id}/`` via :func:`save_cookies`.

    The Facebook password is never persisted — only the resulting session
    cookies (optionally encrypted at rest).

    Returns True when a session was captured and saved, False otherwise
    (login wall / checkpoint / timeout).
    """
    from backend.core.config import get_settings
    from playwright.sync_api import sync_playwright

    if not email or not password:
        logger.warning("login_with_credentials called without credentials")
        return False
    if owner_id is None:
        logger.warning(
            "login_with_credentials requires owner_id; refusing to save "
            "personal cookies to the global store"
        )
        return False

    timeout_seconds = timeout_seconds or get_settings().personal_login_timeout_seconds
    deadline = time.monotonic() + timeout_seconds
    logged_in = False
    session_cookies: list = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = context.new_page()
        try:
            page.goto(
                "https://m.facebook.com/login/",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            # m.facebook renders a lightweight form; fall back to the main
            # site selectors if the login form is missing.
            if not page.query_selector('input[name="email"]'):
                page.goto(
                    "https://www.facebook.com/login/",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            page.fill('input[name="email"]', email)
            page.fill('input[name="pass"]', password)
            page.click('button[name="login"]')
            # Let the redirect settle before polling for the session cookie.
            page.wait_for_timeout(2500)

            while time.monotonic() < deadline:
                cookies = context.cookies()
                fb_cookies = [c for c in cookies if "facebook.com" in c.get("domain", "")]
                if any(c["name"] == "c_user" for c in fb_cookies):
                    logged_in = True
                    session_cookies = cookies
                    break
                time.sleep(2)
        except Exception as exc:  # noqa: BLE001 - surfaces as login failure
            logger.warning("Headless Facebook login raised: %s", exc)
        finally:
            browser.close()

    if logged_in and session_cookies:
        save_cookies(session_cookies, account_name=account_name, owner_id=owner_id)
        fb_cookies = [c for c in session_cookies if "facebook.com" in c.get("domain", "")]
        logger.info(
            "Personal login successful (owner_id=%s account=%s): %d FB cookies",
            owner_id, account_name or "default", len(fb_cookies),
        )
        return True

    logger.warning(
        "Personal login failed (owner_id=%s account=%s): no c_user session within %.0fs",
        owner_id, account_name or "default", timeout_seconds,
    )
    return False


# ---------------------------------------------------------------------------
# Live session capture (remote-debug browser login)
#
# Facebook shows a CAPTCHA on almost every fresh login, so a server-side
# credential login can't be automated. Instead the dashboard's "add session"
# flow launches a throwaway Chromium with a remote-debugging (CDP) endpoint,
# pre-navigates it to Facebook's login page, and returns a pipe link the user
# opens in their own browser tab (Chrome's DevTools frontend). The user signs
# in / solves the CAPTCHA live; this module polls for the ``c_user`` + ``xs``
# session cookies, saves the jar (disk + mirror), then tears the browser down.
#
# CDP is bound to loopback inside the container and never published. The API
# exposes a same-origin, capture_id-gated proxy (/api/accounts/capture/{id}/...)
# so the flow works from any device without ever opening a port. The profile is
# ephemeral and the browser is killed on success, cancel or timeout.
# ---------------------------------------------------------------------------

# Locked decision 2026-09-17: one published host port (SESSION_CAPTURE_PORT)
# means one capture at a time app-wide; concurrent requests get a 409.
_CAPTURES: dict[str, dict] = {}
_CAPTURES_LOCK = threading.Lock()

_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


class CaptureAlreadyActive(Exception):
    """A capture for the same scope/owner is already running."""


class CaptureStartFailed(Exception):
    """The capture browser could not be started."""


def _record_set(record: dict, **fields) -> None:
    with _CAPTURES_LOCK:
        for key, value in fields.items():
            record[key] = value


def start_session_capture(
    *,
    owner_id: int | None,
    account_name: str | None = None,
    scope: str = "me",
    timeout_seconds: float | None = None,
) -> dict:
    """Launch a live session-capture browser and wait for it to come up.

    Launches the worker in a daemon thread and blocks (bounded) until the
    browser is up (or the start fails). The API endpoint composes the
    same-origin DevTools viewer link from the request host afterwards.

    Returns a dict with ``capture_id``, ``name``, ``scope`` and ``expires_at``.
    Raises :class:`CaptureAlreadyActive` when a capture in the same scope is
    already running, or :class:`CaptureStartFailed` on startup failure.
    """
    from backend.core.config import get_settings

    scope = scope if scope in ("ops", "me") else "me"
    timeout_seconds = timeout_seconds or get_settings().session_capture_timeout_seconds
    key = "ops" if scope == "ops" else f"me:{owner_id}"

    with _CAPTURES_LOCK:
        existing = _CAPTURES.get(key)
        if existing and not existing.get("finished"):
            raise CaptureAlreadyActive()
        capture_id = secrets.token_hex(8)
        record = {
            "id": capture_id,
            "key": key,
            "scope": scope,
            "owner_id": owner_id,
            "name": (account_name or "default").strip() or "default",
            "finished": False,
            "url": None,
            "error": None,
            "cancel": False,
            "saved": False,
            "deadline": time.monotonic() + timeout_seconds,
        }
        _CAPTURES[key] = record

    threading.Thread(
        target=_capture_worker,
        args=(record, timeout_seconds),
        daemon=True,
    ).start()

    # Wait (bounded) for the worker to signal the capture browser is ready.
    wait_deadline = time.monotonic() + 20
    while time.monotonic() < wait_deadline:
        with _CAPTURES_LOCK:
            if record.get("ready"):
                return {
                    "capture_id": capture_id,
                    "name": record["name"],
                    "scope": scope,
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
                    ).isoformat(),
                }
            if record.get("error"):
                break
        time.sleep(0.25)

    with _CAPTURES_LOCK:
        record["finished"] = True
    raise CaptureStartFailed(record.get("error") or "capture browser failed to start")


def cancel_session_capture(capture_id: str) -> bool:
    """Request cancellation of a running capture (best-effort).

    The worker notices the flag on its next poll, closes the browser and
    cleans up. Returns True if a running capture was found and flagged.
    """
    with _CAPTURES_LOCK:
        for record in _CAPTURES.values():
            if record["id"] == capture_id and not record.get("finished"):
                record["cancel"] = True
                return True
    return False


def get_capture_record(capture_id: str) -> dict | None:
    """Return the capture record with ``capture_id``, or None.

    Used by the API's CDP proxy/bridge to gate access to an active capture.
    """
    with _CAPTURES_LOCK:
        for record in _CAPTURES.values():
            if record["id"] == capture_id:
                return record
    return None


def _wait_for_cdp(base: str, deadline: float) -> bool:
    """Poll until Chromium's CDP HTTP endpoint answers /json/version."""
    import urllib.request

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/json/version", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _capture_ws_url(base: str) -> str | None:
    """Find the login page target's ``webSocketDebuggerUrl`` via /json.

    Prefers the Facebook page; falls back to the first page target.
    """
    import urllib.request

    try:
        with urllib.request.urlopen(f"{base}/json", timeout=3) as r:
            targets = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    for target in targets:
        if target.get("type") == "page" and "facebook" in (target.get("url") or ""):
            return target.get("webSocketDebuggerUrl")
    for target in targets:
        if target.get("type") == "page":
            return target.get("webSocketDebuggerUrl")
    return None


def _capture_worker(record: dict, timeout_seconds: float) -> None:
    """Run the capture browser in a background daemon thread."""
    from backend.core.config import get_settings

    settings = get_settings()
    port = settings.session_capture_port
    proc = None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            executable = p.chromium.executable_path
            profile = tempfile.mkdtemp(prefix="fb-capture-")
            proc = subprocess.Popen(
                [
                    executable,
                    f"--remote-debugging-port={port}",
                    "--remote-debugging-address=127.0.0.1",
                    f"--user-data-dir={profile}",
                    "--no-sandbox",
                    "--headless=new",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    # The crashpad handler cannot start under the hardened
                    # container (dropped caps) and its failure CHECK-crashes
                    # Chromium at boot — disable crash reporting entirely.
                    "--disable-crash-reporter",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Chromium's crashpad needs a writable HOME; the non-root
                # container user's HOME is not, which made the browser
                # SIGTRAP-crash at boot. Set HOME into the throwaway profile.
                env={
                    **os.environ,
                    "HOME": profile,
                    "XDG_CONFIG_HOME": os.path.join(profile, ".config"),
                },
            )

            cdp_base = f"http://127.0.0.1:{port}"
            if not _wait_for_cdp(cdp_base, deadline=time.monotonic() + 15):
                raise CaptureStartFailed("capture browser CDP endpoint did not come up")

            browser = p.chromium.connect_over_cdp(cdp_base)
            context = browser.contexts[0] if browser.contexts else browser.new_context(
                user_agent=_DESKTOP_UA,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                "https://www.facebook.com/login/",
                wait_until="domcontentloaded",
                timeout=45000,
            )

            ws_url = _capture_ws_url(cdp_base)
            if not ws_url:
                raise CaptureStartFailed("could not resolve the capture page WebSocket")
            # Browser is up and reachable through the CDP proxy — publish
            # readiness. The API endpoint composes the same-origin viewer link
            # from the request host, so it works from any device without
            # exposing the CDP port.
            _record_set(record, ready=True)

            # Poll for the two cookies that define a Facebook session (c_user +
            # xs), giving the user time to log in and solve the CAPTCHA.
            deadline = time.monotonic() + timeout_seconds
            captured: list | None = None
            while time.monotonic() < deadline:
                if record.get("cancel"):
                    break
                cookies = context.cookies()
                fb = [c for c in cookies if "facebook.com" in c.get("domain", "")]
                if any(c["name"] == "c_user" for c in fb) and any(c["name"] == "xs" for c in fb):
                    captured = cookies
                    break
                time.sleep(2)

            if captured:
                save_cookies(captured, account_name=record["name"], owner_id=record["owner_id"])
                _record_set(record, saved=True)
                logger.info(
                    "Session capture complete (%s/%s, %d cookies)",
                    record["scope"], record["name"], len(captured),
                )
            try:
                browser.close()
            except Exception:
                pass
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
    except Exception as exc:  # noqa: BLE001 - always surface in the record
        logger.warning("Session capture failed: %s", exc)
        _record_set(record, error=str(exc))
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        _record_set(record, finished=True)


def fetch_with_browser(
    url: str,
    *,
    max_posts: Optional[int] = None,
    scroll_rounds: int = MAX_SCROLL_ROUNDS,
    cancel_event: Optional[threading.Event] = None,
    use_cookies: bool = True,
    account_name: Optional[str] = None,
    owner_id: Optional[int] = None,
    progress_callback: Optional[Callable[..., None]] = None,
) -> tuple:
    """Load a Facebook page in a headless browser, scroll to load posts,
    and return ``(html, stats)`` where *stats* is a dict with at least
    ``login_wall`` (bool) and ``posts_found`` (int).

    ``owner_id`` selects the personal cookie store when given; ``None``
    reads the global ops store.
    """
    from playwright.sync_api import sync_playwright

    def _report(found: int) -> None:
        if progress_callback:
            try:
                progress_callback(posts_found=found)
            except Exception:
                pass

    html_result = ""
    login_wall_detected = False
    posts_found_total = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )

        # Load saved cookies if available
        if use_cookies:
            cookies = load_cookies(account_name=account_name, owner_id=owner_id)
            if cookies:
                context.add_cookies(cookies)
                logger.info(
                    "Browser: loaded %d cookies (account=%s owner_id=%s)",
                    len(cookies), account_name or "default", owner_id,
                )

        page = context.new_page()

        # Block unnecessary resources to speed up loading
        def route_handler(route):
            if route.request.resource_type in ("image", "media", "font"):
                route.abort()
            else:
                route.continue_()

        page.route("**/*", route_handler)

        try:
            logger.info("Browser: navigating to %s", url)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # Dismiss cookie/login popups if present
            for selector in [
                'button:has-text("Decline optional cookies")',
                'button:has-text("Accept all cookies")',
                'button:has-text("Not Now")',
                'div[role="dialog"] button[aria-label="Close"]',
                '[aria-label="Close"]',
            ]:
                try:
                    el = page.query_selector(selector)
                    if el:
                        el.click()
                        page.wait_for_timeout(500)
                except Exception:
                    pass

            # Check if we hit a login wall
            login_check = page.evaluate("""
                () => {
                    const url = window.location.href;
                    const hasLoginForm = !!document.querySelector('input[name="email"]');
                    const isLoginPage = url.includes('login') || url.includes('checkpoint');
                    return {url, hasLoginForm, isLoginPage};
                }
            """)
            if login_check.get("isLoginPage") or login_check.get("hasLoginForm"):
                login_wall_detected = True
                logger.warning("Browser: hit login wall at %s", login_check.get("url"))
                if not load_cookies(account_name=account_name, owner_id=owner_id):
                    print("  WARNING: Hit Facebook login wall. Run 'python cli.py login' first.")

            # Page hub layout: click the "All" / "Posts" timeline tab so the
            # real feed renders, else scroll only works on a thin stub.
            try:
                clicked = page.evaluate("""
                    () => {
                        const els = Array.from(document.querySelectorAll('[role="tab"]'));
                        const t = els.find(e => /^\\s*(All|Posts)\\s*$/i.test((e.innerText || "").trim()));
                        if (t) { t.click(); return true; }
                        return false;
                    }
                """)
                if clicked:
                    page.wait_for_timeout(1500)
            except Exception:
                pass

            # Scroll to load more posts. Facebook virtualizes the feed: only a
            # few cards stay mounted at a time while scrolling, so we must
            # snapshot each round and accumulate the unique post containers
            # rather than grab a single final page.content() (which would only
            # hold whatever is mounted at scroll-end).
            def _snapshot_fingerprint(text: str) -> str:
                return hashlib.sha1(
                    text.encode("utf-8", "surrogatepass")
                ).hexdigest()[:24]

            # Facebook's Comet feed loads the next batch of stories via
            # POST /api/graphql/ responses, not new DOM in the page.  Capture
            # those payloads (they carry full post IDs, timestamps, texts and
            # engagement counts) and embed them into the returned snapshot.
            graphql_payloads: List[str] = []
            graphql_post_ids: set = set()

            def _on_response(response):
                try:
                    if not response.url.endswith("/api/graphql/"):
                        return
                    body = response.text()
                except Exception:
                    return
                if '"post_id"' not in body or "creation_time" not in body:
                    return
                new_ids = set(
                    re.findall(r'"post_id"\s*:\s*"(\d+)"', body)
                )
                if not new_ids - graphql_post_ids:
                    return
                graphql_post_ids.update(new_ids)
                graphql_payloads.append(body)

            page.on("response", _on_response)

            dom_pool: List[str] = []
            dom_seen: set = set()
            script_pool: List[str] = []
            script_seen: set = set()
            stale_rounds = 0

            for i in range(scroll_rounds):
                if cancel_event and cancel_event.is_set():
                    break

                gql_before = len(graphql_post_ids)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(int(SCROLL_DELAY * 1000))

                soup = BeautifulSoup(page.content(), "lxml")
                roots = find_post_roots(soup)
                fresh_new = 0

                for root in roots:
                    aria = root.get("aria-label") or ""
                    if aria.startswith("Comment by"):
                        continue
                    text = root.get_text(" ", strip=True)[:4000]
                    if not text or len(text) < 20:
                        continue
                    # Drop comment previews rendered inside the feed (they look
                    # like "Name · 2h … Like Reply").
                    if re.search(r"\bLike\s*Reply\b", text):
                        continue
                    key = _snapshot_fingerprint(text)
                    if key in dom_seen:
                        continue
                    dom_seen.add(key)
                    dom_pool.append(str(root))
                    fresh_new += 1

                # Capture embedded Comet/Relay JSON that carries post data.
                for tag in soup.find_all("script"):
                    data = tag.string or ""
                    if '"post_id"' in data and len(data) < 200_000:
                        key = _snapshot_fingerprint(data)
                        if key in script_seen:
                            continue
                        script_seen.add(key)
                        script_pool.append(str(tag))
                        fresh_new += 1

                # When the Comet feed is unreachable (login wall), the only
                # posts come from the rendered DOM; keep scrolling longer
                # instead of giving up after only 3 quiet rounds.
                # When graphql is present but we're still below the target,
                # be more patient — Facebook sometimes resumes after a pause.
                current = len(dom_pool) + len(script_pool) + len(graphql_post_ids)
                if graphql_post_ids:
                    deficit = max(0, (max_posts or 9999) - current)
                    stale_limit = max(3, min(deficit // 3, 12))
                else:
                    stale_limit = 6
                if fresh_new == 0 and len(graphql_post_ids) == gql_before:
                    stale_rounds += 1
                    if stale_rounds >= stale_limit:
                        logger.info(
                            "Browser: no new posts after %d scrolls, stopping",
                            stale_rounds,
                        )
                        break
                else:
                    stale_rounds = 0
                    logger.info(
                        "Browser: scroll %d - %d posts accumulated "
                        "(%d DOM, %d script, %d graphql responses, %d post_ids)",
                        i + 1,
                        len(dom_pool) + len(script_pool) + len(graphql_post_ids),
                        len(dom_pool),
                        len(script_pool),
                        len(graphql_payloads),
                        len(graphql_post_ids),
                    )
                    _report(
                        len(dom_pool) + len(script_pool) + len(graphql_post_ids)
                    )
                    posts_found_total = max(
                        posts_found_total,
                        len(dom_pool) + len(script_pool) + len(graphql_post_ids),
                    )

                if max_posts is not None and (
                    len(dom_pool) + len(graphql_post_ids) >= max_posts
                ):
                    logger.info(
                        "Browser: reached %d posts (target: %d)",
                        len(dom_pool) + len(graphql_post_ids), max_posts,
                    )
                    break

            gql_blocks = "".join(
                '<script type="application/json" data-fb-graphql-feed="1">'
                + payload
                + "</script>"
                for payload in graphql_payloads
            )
            # Append the final fully-scrolled DOM too. The accumulated roots
            # and graphql blocks usually carry enough, but Facebook's feed is
            # heavily virtualized and the last page state holds the freshest
            # React/Relay store (scripts far larger than the snapshot cap) —
            # the parser extracts far more from the whole document.
            try:
                final_dom = page.content()
            except Exception:
                final_dom = ""
            # Marker: the Comet feed (graphql blocks generally carry the real
            # timeline) never loaded.  A handful of DOM stubs alone means a
            # partial/walled view, and the caller should not report it as a
            # clean scrape.
            feed_marker = (
                "<!-- fb-scrape-feed-missing -->"
                if not graphql_payloads
                else ""
            )
            html_result = (
                "<html><body>"
                + "".join(dom_pool)
                + "".join(script_pool)
                + gql_blocks
                + feed_marker
                + final_dom
                + "</body></html>"
            )
            logger.info(
                "Browser: accumulated snapshot %d bytes (%d DOM, %d script, "
                "%d graphql blocks)",
                len(html_result), len(dom_pool), len(script_pool),
                len(graphql_payloads),
            )

        except Exception as exc:
            logger.warning("Browser error: %s", exc)
        finally:
            browser.close()

    return html_result, {
        "login_wall": login_wall_detected,
        "posts_found": posts_found_total,
    }


def _clean_dom_text(text: str) -> str:
    """Strip FB page chrome from a DOM post's text so it can be matched
    against the clean GraphQL copy for duplicate detection."""
    for phrase in ("Verified account", "Shared with Public", "Instagram"):
        text = text.replace(phrase, "")
    return re.sub(r"\s+", " ", text).strip().lower()


def _drop_dom_duplicates(posts, clean=_clean_dom_text):
    """Drop DOM-only posts (``post_id=None``) whose cleaned text overlaps an
    already-accepted post with a ``post_id``.  Keeps the rich GraphQL/script
    copy and drops the chrome-wrapped DOM duplicate."""
    accepted = []
    seen_texts = []
    for post in posts:
        if post.post_id:
            accepted.append(post)
            if post.text:
                seen_texts.append(clean(post.text))
            continue
        # no post_id: keep only if it doesn't duplicate accepted content
        if post.text:
            ctext = clean(post.text)
            if any(ctext in other or other in ctext for other in seen_texts):
                logger.info(
                    "Browser parse: dropped DOM duplicate (text overlaps GraphQL copy)"
                )
                continue
        accepted.append(post)
    return accepted


def parse_browser_page(
    html: str,
    page_url: str,
    handle: Optional[str] = None,
) -> "ParsedPage":
    """Parse browser-rendered HTML using DOM + script extraction."""
    from backend.scraper.parser import (
        ParsedPage,
        ParsedPost,
        parse_page,
        _parse_post_root,
        _clean_name,
        _meta_content,
        _page_id_from_url,
    )

    if not html or not html.strip():
        return ParsedPage(page_name=handle, fetched_url=page_url)

    soup = BeautifulSoup(html, "lxml")

    og_title = _clean_name(_meta_content(soup, ("property", "og:title"), ("name", "og:title")))
    og_image = _meta_content(soup, ("property", "og:image"), ("name", "og:image"))
    og_url = _meta_content(soup, ("property", "og:url"), ("name", "og:url"))

    page_name = og_title or handle
    profile_url = og_url or page_url
    page_id = _page_id_from_url(profile_url) or _page_id_from_url(page_url)

    posts = []
    post_errors = []

    # Always try DOM-based extraction first
    roots = find_post_roots(soup)
    dom_posts = []
    if roots:
        for root in roots:
            try:
                dom_posts.append(_parse_post_root(root, page_url))
            except Exception as exc:
                post_errors.append({
                    "post_url": "",
                    "code": "extraction_failure",
                    "message": f"DOM parse failed: {exc!r}",
                })

    # Always try script-based extraction (modern Facebook embeds JSON in scripts)
    from backend.scraper.parser import _extract_posts_from_scripts
    script_posts = []
    try:
        script_posts = _extract_posts_from_scripts(html, page_url)
    except Exception as exc:
        post_errors.append({
            "post_url": "",
            "code": "extraction_failure",
            "message": f"script extraction failed: {exc!r}",
        })

    # Rich Comet feed payloads captured from the browser's own /api/graphql/
    # requests (post IDs, exact timestamps, texts and engagement counts).
    from backend.scraper.parser import extract_posts_from_graphql
    gql_posts = []
    try:
        gql_posts = extract_posts_from_graphql(html, page_url)
    except Exception as exc:
        post_errors.append({
            "post_url": "",
            "code": "extraction_failure",
            "message": f"graphql extraction failed: {exc!r}",
        })

    # Merge: use graphql posts as primary (richest data), then script posts
    # whose id isn't already covered, then DOM posts not yet seen.
    seen_ids = set()
    for post in gql_posts:
        if post.post_id:
            seen_ids.add(post.post_id)
        posts.append(post)

    for post in script_posts:
        if post.post_id and post.post_id in seen_ids:
            continue
        if post.post_id:
            seen_ids.add(post.post_id)
        posts.append(post)

    for post in dom_posts:
        if post.post_id and post.post_id in seen_ids:
            continue
        seen_ids.add(post.post_id or "")
        posts.append(post)

    # Deduplicate by post_id
    seen = set()
    unique_posts = []
    for post in posts:
        pid = post.post_id
        if pid and pid in seen:
            continue
        seen.add(pid)
        unique_posts.append(post)

    # Drop unidentifiable containers (FB renders empty teaser divs)
    def _has_signal(post) -> bool:
        return bool(post.post_id or post.text or post.thumbnail_url
                    or post.media_url or post.video_url
                    or post.has_image or post.has_video)
    unique_posts = [p for p in unique_posts if _has_signal(p)]

    # DOM duplicates: DOM roots carry post_id=None, so the post_id dedup above
    # can't catch them.  A DOM snapshot of a post already captured via GraphQL
    # shows the same text (wrapped in page chrome like "Verified account" /
    # "Shared with Public").  If a comment-less post's cleaned text overlaps an
    # already-accepted post, drop it.
    unique_posts = _drop_dom_duplicates(unique_posts)

    logger.info(
        "Browser parse: %d DOM roots, %d posts (deduped), %d errors",
        len(roots), len(unique_posts), len(post_errors),
    )

    return ParsedPage(
        page_name=page_name,
        page_id=page_id,
        profile_url=profile_url,
        og_image=og_image,
        posts=unique_posts,
        post_errors=post_errors,
        fetched_url=page_url,
    )


def _browser_node_enabled() -> bool:
    """True when browser-mode captures should run through node
    (``USE_NODE_BROWSER``, finalplanv2 §4 browser-mode / §12).

    Independent of the http-mode ``use_node`` seam: both must be verified
    against the legacy Python path before any cutover (§14 parity gates).
    """
    from backend.core.config import get_settings
    return get_settings().use_node_browser


def _browser_fetch(
    url: str,
    *,
    max_posts: Optional[int] = None,
    scroll_rounds: int = MAX_SCROLL_ROUNDS,
    cancel_event: Optional[threading.Event] = None,
    use_cookies: bool = True,
    account_name: Optional[str] = None,
    owner_id: Optional[int] = None,
    progress_callback: Optional[Callable[..., None]] = None,
) -> tuple:
    """Dispatch one browser capture: node (``USE_NODE_BROWSER``) or Python.

    Both paths return ``(html, stats)`` with the same tuple contract
    (``stats`` = ``login_wall``/``posts_found``), so the 3-attempt retry
    ladder in :func:`scrape_source_browser` is identical for either backend.
    The flag-off path calls the module-global ``fetch_with_browser`` so the
    existing wall-handling tests that ``patch.object(bs, "fetch_with_browser")``
    keep intercepting.
    """
    if _browser_node_enabled():
        from backend.services.node_browser import fetch_browser_via_node
        return fetch_browser_via_node(
            url,
            max_posts=max_posts,
            scroll_rounds=scroll_rounds,
            cancel_event=cancel_event,
            account_name=account_name,
            owner_id=owner_id,
            use_cookies=use_cookies,
            progress_callback=progress_callback,
        )
    return fetch_with_browser(
        url,
        max_posts=max_posts,
        scroll_rounds=scroll_rounds,
        cancel_event=cancel_event,
        account_name=account_name,
        owner_id=owner_id,
        use_cookies=use_cookies,
        progress_callback=progress_callback,
    )


def scrape_source_browser(
    url: str,
    *,
    max_posts: Optional[int] = None,
    scroll_rounds: Optional[int] = None,
    account_name: Optional[str] = None,
    owner_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    post_type: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[..., None]] = None,
) -> "SourceResult":
    """Scrape one source through the headless browser (GraphQL feed) and return
    a :class:`~backend.scraper.SourceResult` with canonical normalized posts.

    Mirrors the CLI ``--browser`` path so the job worker and the CLI share one
    entry point.  ``scroll_rounds`` defaults to :data:`MAX_SCROLL_ROUNDS`.
    ``start_date`` / ``end_date`` / ``post_type`` filter the results exactly
    like the HTTP path (posts without a proven timestamp are skipped when a
    date range is given).

    ``account_name`` + ``owner_id`` select which cookie scope to unlock:
    ``owner_id=None`` reads the global ops pool; otherwise the account is
    resolved from the owner's personal store.
    """
    from backend.scraper import ScrapeOptions, SourceResult, _handle_of, _passes_filters, validate_or_raise
    from backend.scraper.dedup import dedup_posts
    from backend.scraper.normalizer import normalize_post

    def _is_wall(html: str) -> bool:
        if not html:
            return True
        # Real feed content is the strongest signal — embedded React/Relay
        # JSON contains "post_id" references; a wall has none.
        if '"post_id"' in html:
            return False
        # A large rendered document that isn't the full feed but has no
        # post markers (e.g. a fully-formed page) is still not a login wall.
        if len(html) > 100000:
            return False
        # classic login wall markers on small stub pages
        if 'input name="email"' in html or 'id="email"' in html:
            return True
        if 'checkpoint' in html.lower() and 'login' in html.lower():
            return True
        return True

    errors: List[Dict[str, str]] = []
    try:
        normalized_url = validate_or_raise(url)
    except Exception as exc:  # invalid URL -> per-job validation error
        return SourceResult(
            url=url,
            errors=[{"url": url, "code": "invalid_url", "message": str(exc)}],
        )

    # Try up to 3 times — Facebook sometimes serves a login wall (or an
    # empty/partial feed) on first load.  Attempts 1-2 use the saved session
    # cookies (a fresh session usually unlocks the full Comet feed); attempt
    # 3 falls back anonymous for pages that render without a session.  A
    # short delay between attempts helps avoid tripping Facebook's rate
    # throttle on repeated headless loads.  After all attempts, surface the
    # cookie expiry status clearly so the caller can prompt a re-login.
    html = ""
    wall_hit = False
    stats: Dict[str, object] = {}

    for attempt in range(3):
        use_cookies = (attempt < 2) and account_name is not None
        html, stats = _browser_fetch(
            normalized_url,
            max_posts=max_posts,
            scroll_rounds=scroll_rounds if scroll_rounds is not None else MAX_SCROLL_ROUNDS,
            cancel_event=cancel_event,
            account_name=account_name,
            owner_id=owner_id,
            use_cookies=use_cookies,
            progress_callback=progress_callback,
        )

        wall_hit = stats.get("login_wall", False) or _is_wall(html)

        if not wall_hit:
            break  # success, don't overwrite with a worse attempt

        if attempt == 0 and wall_hit:
            logger.warning("Login wall with account %s, retrying with cookies...", account_name or "default")
        elif attempt == 1 and wall_hit:
            logger.warning("Login wall with account %s, retrying anonymous...", account_name or "default")
        if attempt < 2:
            time.sleep(3)

    # Surface BUG-004 clearly: if we ended with a wall AND the saved
    # cookies are expired, tell the operator exactly what to do.
    if wall_hit and account_name and get_cookie_status(account_name, owner_id=owner_id) == "EXPIRED":
        logger.error(
            "Account %s cookies EXPIRED (owner_id=%s). Run: python cli.py login --account %s",
            account_name, owner_id, account_name,
        )

    if not html:
        return SourceResult(
            url=url,
            errors=[
                {"url": url, "code": "fetch_failed",
                 "message": "Browser returned empty HTML after retries"}
            ],
        )
    if _is_wall(html):
        return SourceResult(
            url=url,
            errors=[
                {"url": url, "code": "login_wall",
                 "message": "Facebook login wall still present after retries. Run 'python cli.py login' to refresh cookies."}
            ],
        )

    page = parse_browser_page(
        html,
        page_url=normalized_url,
        handle=_handle_of(normalized_url),
    )
    # The saved session page never yielded the GraphQL feed (only DOM
    # stubs).  Surface it as a partial result instead of a clean scrape so
    # the caller knows the post set is incomplete.
    if "fb-scrape-feed-missing" in html and page.posts:
        logger.warning(
            "Browser: feed missing for %s — only %d DOM-only post(s) recovered",
            normalized_url, len(page.posts),
        )
        errors.append({
            "url": url,
            "code": "partial_feed",
            "message": (
                "Facebook's timeline feed was not loaded (login wall / "
                "limited session); only DOM-rendered posts were recovered. "
                "Refresh cookies with 'python cli.py login' and retry."
            ),
        })
    if progress_callback:
        try:
            progress_callback(posts_found=len(page.posts), posts_extracted=len(page.posts))
        except Exception:
            pass

    normalized: List[Dict[str, Any]] = []
    for parsed_post in page.posts:
        try:
            normalized.append(
                normalize_post(
                    parsed_post,
                    page_name=page.page_name,
                    page_id=page.page_id,
                    facebook_url=normalized_url,
                )
            )
        except Exception as exc:
            errors.append({
                "post_url": parsed_post.post_url or url,
                "code": "extraction_failure",
                "message": f"normalize failed: {exc!r}",
            })

    kept, duplicates = dedup_posts(normalized)

    filters = ScrapeOptions(
        urls=[url],
        start_date=start_date,
        end_date=end_date,
        post_type=post_type,
    )
    kept = [p for p in kept if _passes_filters(p, filters)]

    if max_posts is not None and len(kept) > max_posts:
        kept = kept[:max_posts]

    if progress_callback:
        try:
            progress_callback(
                posts_found=len(page.posts),
                posts_extracted=len(kept),
                duplicates_removed=duplicates,
                posts_failed=len(page.post_errors) + len(errors),
            )
        except Exception:
            pass

    return SourceResult(
        url=url,
        page_name=page.page_name,
        page_id=page.page_id,
        posts=kept,
        stats={
            "posts_discovered": len(page.posts),
            "posts_extracted": len(kept),
            "duplicates_removed": duplicates,
            "posts_skipped": 0,
            "posts_failed": len(page.post_errors) + len(errors),
        },
        errors=[dict(e) for e in page.post_errors] + errors,
    )
