"""Saved Facebook session (cookie) accounts endpoints.

Two scopes (locked decision, 2026-09-17):

* ``ops`` — the global cookie pool maintained by the operator via the CLI
  (``cli.py login --account``) or the ops role. Any signed-in user may list
  and *use* ops sessions; only the ops role may delete them.
* ``me`` — per-user sessions stored under ``data/personal/{uid}/``. Visible,
  usable and deletable only by their owner. Personal sessions are captured
  server-side via :func:`~backend.scraper.browser_scraper.login_with_credentials`
  (the "+" flow in Saved Accounts); the Facebook password is never stored.

Endpoints
---------
* GET    /api/accounts                      — ``{ops: [...], mine: [...]}`` (authed)
* POST   /api/accounts/capture              — start a live session capture; returns a
                                              same-origin viewer link the user opens to
                                              log into Facebook in a new tab (authed;
                                              ``ops`` scope requires the ops role)
* DELETE /api/accounts/capture/{id}         — abort a running capture (authed)
* POST   /api/accounts/personal             — label + FB credentials → server-side
                                              login, save a personal session (authed)
* DELETE /api/accounts/{scope}/{name}       — remove a session (scope + role gated)
* POST   /api/accounts/cookies-txt           — add a session by pasting an exported
                                              cookies.txt (Netscape format; parsed
                                              server-side, scope + role rules as above)

Capture viewer (capability-based, same-origin):
* GET /api/accounts/capture/{id}/viewer           — the live login page viewer
                                                   (screenshot stream + input pipe)
* GET /api/accounts/capture/{id}/devtools/{path} — proxy the capture browser's
                                                   DevTools frontend assets
* GET /api/accounts/capture/{id}/json/{path}     — proxy Chrome CDP /json endpoints
* WS  /api/accounts/capture/{id}/cdp             — bridge the capture browser's CDP
                                                   websocket (single client)
The unguessable ``capture_id`` is the credential; CDP itself stays on
loopback inside the backend container and is never published.
"""
from __future__ import annotations

import asyncio

import httpx
import websockets
from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session
from websockets.asyncio.client import ClientConnection, connect as _cdp_connect

from backend.api.capture_viewer import VIEWER_HTML
from backend.auth.dependencies import get_current_user
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.exceptions import AppError, NotFoundError
from backend.core.plans import personal_account_cap
from backend.models.user import User
from backend.schemas.accounts import (
    AccountOut,
    AccountsResponse,
    CookiesTxtRequest,
    PersonalLoginRequest,
    SessionCaptureOut,
    SessionCaptureRequest,
)
from backend.scraper.browser_scraper import (
    CaptureAlreadyActive,
    CaptureStartFailed,
    _capture_ws_url,
    account_metadata,
    cancel_session_capture,
    delete_account,
    get_capture_record,
    get_cookie_status,
    list_accounts,
    login_with_credentials,
    save_cookies,
    start_session_capture,
)
from backend.scraper.cookies_txt import (
    InvalidCookiesTxt,
    parse_cookies_txt,
    require_facebook_session,
)

router = APIRouter(tags=["accounts"])


# ---------------------------------------------------------------------------
# Same-origin session-capture viewer (CDP proxy + websocket bridge)
# ---------------------------------------------------------------------------


def _cdp_base() -> str:
    """Loopback base of the running capture Chromium's CDP endpoint."""
    return f"http://127.0.0.1:{get_settings().session_capture_port}"


def _capture_live(capture_id: str) -> bool:
    """True while a capture with this id exists and is still running."""
    record = get_capture_record(capture_id)
    return bool(record and not record.get("finished"))


def _build_capture_link(request: Request, capture_id: str) -> str:
    """Compose the same-origin live login viewer link for a capture.

    The viewer HTML is served by the backend (same origin) and derives its
    websocket target from ``window.location``, so the whole flow stays on the
    app's own origin and scheme (``https``/``wss`` behind TLS, ``http``/``ws``
    in dev) — no CDP port is ever exposed. The unguessable ``capture_id``
    gates access.
    """
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/accounts/capture/{capture_id}/viewer"


_PROXY_STRIP_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection", "keep-alive"}
)


async def _stream_upstream(url: str) -> StreamingResponse:
    """Stream an upstream GET response (Chrome's CDP HTTP endpoints)."""

    async def _close() -> None:
        await response.aclose()
        await client.aclose()

    client = httpx.AsyncClient()
    try:
        request = client.build_request("GET", url, headers={"Accept-Encoding": "identity"})
        response = await client.send(request, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in _PROXY_STRIP_HEADERS and key.lower() != "content-type"
    }
    return StreamingResponse(
        response.aiter_bytes(),
        status_code=response.status_code,
        headers=headers,
        media_type=response.headers.get("content-type"),
        background=_close,
    )


async def _try_bridge_lock(record: dict) -> bool:
    """Grab the capture's single-bridge lock without waiting.

    ``asyncio.wait_for(lock.acquire(), timeout=0)`` races on a freshly
    created lock, so check-then-acquire is used instead (``acquire``'s fast
    path never suspends, keeping the check+acquire atomic per loop turn).
    """
    lock = record.setdefault("_bridge_lock", asyncio.Lock())
    if lock.locked():
        return False
    await lock.acquire()
    return True


async def _pump_ws_bridge(websocket: WebSocket, cdp: ClientConnection) -> None:
    """Forward websocket frames both ways between viewer and CDP endpoint."""

    async def cdp_to_client() -> None:
        async for message in cdp:
            if isinstance(message, str):
                await websocket.send_text(message)
            else:
                await websocket.send_bytes(message)

    async def client_to_cdp() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            if text is not None:
                await cdp.send(text)
                continue
            data = message.get("bytes")
            if data is not None:
                await cdp.send(data)

    tasks = (asyncio.create_task(cdp_to_client()), asyncio.create_task(client_to_cdp()))
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _normalize_scope(scope: str) -> str:
    scope = scope.strip().lower()
    if scope not in ("ops", "me"):
        raise AppError(
            "Account scope must be 'ops' or 'me'",
            status_code=400,
            code="invalid_scope",
        )
    return scope


def _account_out(item: dict, scope: str, owner_id: int | None = None) -> AccountOut:
    return AccountOut(
        name=item["name"],
        scope=scope,
        saved_at=item.get("saved_at"),
        cookies_file=item.get("cookies_file"),
        status=get_cookie_status(item["name"], owner_id=owner_id),
    )


@router.get(
    "/accounts",
    response_model=AccountsResponse,
    summary="List saved Facebook sessions (ops pool + my own)",
)
def list_saved_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountsResponse:
    """List operator-maintained sessions and the caller's personal sessions.

    Metadata only — cookie contents are never returned. A caller always sees
    the full ops pool (shared) but only their own ``me`` sessions.
    """
    ops_items = [_account_out(item, "ops") for item in account_metadata()]
    mine_items = [
        _account_out(item, "me", owner_id=current_user.id)
        for item in account_metadata(owner_id=current_user.id)
    ]
    return AccountsResponse(ops=ops_items, mine=mine_items)


@router.post(
    "/accounts/personal",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add my own Facebook session via server-side login",
)
def add_personal_account(
    payload: PersonalLoginRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountOut:
    """Run a headless Facebook login on the user's behalf and save the
    resulting session cookies under the caller's personal store.

    The Facebook credentials are used once and never persisted. Login
    failures (checkpoint / timeout / wrong password) return 502.
    """
    name = payload.name.strip()
    if not name:
        raise AppError("Account name cannot be empty", status_code=400, code="invalid_input")

    cap = personal_account_cap(current_user.plan)
    if cap is not None and len(list_accounts(owner_id=current_user.id)) >= cap:
        raise AppError(
            f"Your {current_user.plan} plan allows {cap} personal account(s); "
            "delete one or upgrade to add more",
            status_code=429,
            code="plan_limit",
        )

    ok = login_with_credentials(
        email=payload.email,
        password=payload.password,
        owner_id=current_user.id,
        account_name=name,
    )
    if not ok:
        raise AppError(
            "Facebook login failed (wrong credentials, checkpoint, or timeout). "
            "Please try again.",
            status_code=502,
            code="facebook_login_failed",
        )

    # Re-read so the response reflects what was actually stored.
    item = next((i for i in account_metadata(owner_id=current_user.id) if i["name"] == name), None)
    if item is None:
        raise AppError(
            "Login reported success but cookies were not saved",
            status_code=500,
            code="cookie_save_failed",
        )
    return _account_out(item, "me", owner_id=current_user.id)


@router.post(
    "/accounts/capture",
    response_model=SessionCaptureOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start a live Facebook session capture (open the pipe link in a new tab)",
)
def start_capture(
    payload: SessionCaptureRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SessionCaptureOut:
    """Launch a throwaway Chromium whose DevTools viewer is proxied same-origin
    and return the link the caller opens in a new tab. The user signs in to
    Facebook there (solving any CAPTCHA live); the backend captures the
    ``c_user`` + ``xs`` session cookies and saves the jar automatically.

    Scope rules match the rest of the accounts API: ``me`` is open to any
    signed-in user (plan-capped, one capture at a time); ``ops`` requires the
    ops role and targets the shared pool.
    """
    name = payload.name.strip()
    if not name:
        raise AppError("Account name cannot be empty", status_code=400, code="invalid_input")

    if payload.scope == "ops":
        if current_user.role != "ops":
            raise AppError(
                "Only operators may add shared sessions",
                status_code=403,
                code="admin_required",
            )
        owner_id = None
    else:
        cap = personal_account_cap(current_user.plan)
        if cap is not None and len(list_accounts(owner_id=current_user.id)) >= cap:
            raise AppError(
                f"Your {current_user.plan} plan allows {cap} personal account(s); "
                "delete one or upgrade to add more",
                status_code=429,
                code="plan_limit",
            )
        owner_id = current_user.id

    try:
        info = start_session_capture(
            owner_id=owner_id,
            account_name=name,
            scope=payload.scope,
        )
    except CaptureAlreadyActive:
        raise AppError(
            "A session capture is already running — finish it or wait for it to time out",
            status_code=409,
            code="capture_active",
        )
    except CaptureStartFailed as exc:
        raise AppError(
            f"Could not start the capture browser: {exc}",
            status_code=502,
            code="capture_start_failed",
        )
    info["url"] = _build_capture_link(request, info["capture_id"])
    return SessionCaptureOut(**info)


@router.delete(
    "/accounts/capture/{capture_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Abort a running session capture",
)
def cancel_capture(
    capture_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Best-effort cancellation: the worker stops polling, closes the browser
    and cleans up. Unknown or already-finished captures are a no-op 204."""
    cancel_session_capture(capture_id)
    return Response(status_code=204)


@router.post(
    "/accounts/cookies-txt",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a saved Facebook session by pasting cookies.txt",
)
def add_account_from_cookies_txt(
    payload: CookiesTxtRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountOut:
    """Parse an exported cookies.txt (Netscape format) into a saved session.

    Scope rules match the rest of the API: ``me`` is open to any signed-in
    user (plan-capped); ``ops`` requires the ops role and targets the shared
    pool. The raw cookies.txt is never stored — only the parsed jar, persisted
    through the same ``save_cookies`` path as every other capture.
    """
    name = payload.name.strip()
    if not name:
        raise AppError("Account name cannot be empty", status_code=400, code="invalid_input")

    if payload.scope == "ops":
        if current_user.role != "ops":
            raise AppError(
                "Only operators may add shared sessions",
                status_code=403,
                code="admin_required",
            )
        owner_id = None
    else:
        cap = personal_account_cap(current_user.plan)
        if cap is not None and len(list_accounts(owner_id=current_user.id)) >= cap:
            raise AppError(
                f"Your {current_user.plan} plan allows {cap} personal account(s); "
                "delete one or upgrade to add more",
                status_code=429,
                code="plan_limit",
            )
        owner_id = current_user.id

    try:
        cookies = parse_cookies_txt(payload.cookies_txt)
        require_facebook_session(cookies)
    except InvalidCookiesTxt as exc:
        raise AppError(str(exc), status_code=400, code="invalid_cookies_file") from exc

    save_cookies(cookies, account_name=name, owner_id=owner_id)

    item = next((i for i in account_metadata(owner_id=owner_id) if i["name"] == name), None)
    if item is None:
        raise AppError(
            "Cookies were parsed but the session was not saved",
            status_code=500,
            code="cookie_save_failed",
        )
    return _account_out(item, payload.scope, owner_id)


@router.websocket("/accounts/capture/{capture_id}/cdp")
async def capture_cdp_bridge(websocket: WebSocket, capture_id: str) -> None:
    """Bridge the server-side capture browser's CDP websocket to the viewer.

    Capability-based: the unguessable ``capture_id`` in the URL is the
    credential. The socket is refused (never accepted) when the capture is
    unknown/finished, or when another client is already attached.
    """
    record = get_capture_record(capture_id)
    if not record or record.get("finished"):
        await websocket.close(code=4404)
        return
    target = _capture_ws_url(_cdp_base())
    if not target:
        await websocket.close(code=4404)
        return
    if not await _try_bridge_lock(record):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    try:
        async with _cdp_connect(target, max_size=64 * 1024 * 1024) as cdp:
            await _pump_ws_bridge(websocket, cdp)
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass
    except Exception:  # noqa: BLE001 - bridge teardown is best-effort
        pass
    finally:
        lock = record.get("_bridge_lock")
        if lock is not None and lock.locked():
            lock.release()


@router.get(
    "/accounts/capture/{capture_id}/devtools/{path:path}",
    response_class=StreamingResponse,
    summary="Proxy the capture browser's DevTools frontend assets",
)
async def capture_devtools_proxy(capture_id: str, path: str) -> StreamingResponse:
    """Stream the DevTools frontend served by the live capture Chromium.

    Only reachable for an active capture; the unguessable ``capture_id`` is
    the capability. The frontend itself holds no session data — the websocket
    bridge is where the actual page lives.
    """
    if not _capture_live(capture_id):
        raise NotFoundError("Capture is not active or does not exist")
    return await _stream_upstream(f"{_cdp_base()}/devtools/{path}")


@router.get(
    "/accounts/capture/{capture_id}/json/{path:path}",
    response_class=StreamingResponse,
    include_in_schema=False,
    summary="Proxy Chrome's CDP /json endpoints for the DevTools frontend",
)
async def capture_json_proxy(capture_id: str, path: str) -> StreamingResponse:
    if not _capture_live(capture_id):
        raise NotFoundError("Capture is not active or does not exist")
    return await _stream_upstream(f"{_cdp_base()}/json/{path}")


@router.get(
    "/accounts/capture/{capture_id}/viewer",
    response_class=HTMLResponse,
    summary="Serve the live login page viewer for an active capture",
)
async def capture_viewer_page(capture_id: str) -> HTMLResponse:
    """Serve the embedded live-page viewer for an active capture.

    The page shows the capture browser's actual Facebook page (screenshot
    stream) and forwards the user's clicks/keys over the bridge, so opening a
    capture is like a visible ``cli.py login`` browser — through the app. Like
    every capture route, availability is gated on the unguessable
    ``capture_id`` and on the capture still being live.
    """
    if not _capture_live(capture_id):
        raise NotFoundError("Capture is not active or does not exist")
    return HTMLResponse(VIEWER_HTML)


@router.delete(
    "/accounts/{scope}/{account_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a saved Facebook session",
)
def remove_account(
    scope: str,
    account_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Delete an account's cookies + index entry.

    * scope ``me``  -> owner-only (any authenticated user, their own sessions)
    * scope ``ops`` -> ops role required (operator-managed pool)
    """
    return _delete_for_scope(
        scope=_normalize_scope(scope),
        name=account_name,
        current_user=current_user,
        is_ops=current_user.role == "ops",
    )


def _delete_for_scope(scope: str, name: str, current_user: User, is_ops: bool) -> Response:
    """Shared delete body — validates scope/role, then removes the session."""
    name = name.strip()
    if not name:
        raise AppError("Account name cannot be empty", status_code=400, code="invalid_input")

    if scope == "ops":
        if not is_ops:
            raise AppError(
                "Only operators may delete ops-pool sessions",
                status_code=403,
                code="admin_required",
            )
        removed = delete_account(name, owner_id=None)
    else:
        # "me" — the caller may only touch their own personal sessions.
        removed = delete_account(name, owner_id=current_user.id)

    if not removed:
        raise NotFoundError(f"Account '{name}' not found")
    return Response(status_code=204)