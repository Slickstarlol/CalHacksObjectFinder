"""Sign-up / sign-in endpoints and the auth dependencies every route uses.

    POST /api/auth/register  {username, display_name, password}  -> {token, user, ...}
    POST /api/auth/login     {username, password}                -> {token, user, ...}
    POST /api/auth/logout    (Bearer)                            -> revokes the token
    GET  /api/auth/me        (Bearer)                            -> the signed-in user

Token transport: `Authorization: Bearer <token>` on every API call. The only
exception is media that a browser loads through <img src> (snapshots, the
live camera frame/MJPEG stream), where a page cannot set headers: those GET
routes also accept `?token=<token>` (see media_user). Bearer tokens (not
cookies) mean no CSRF surface: another site can't make the browser attach
them.

Devices: the camera process (webcam.py) is a trusted local device, not a
user. Device-only routes accept a request when either
  * it carries `X-RecallAI-Device-Key` equal to env RECALLAI_DEVICE_KEY, or
  * it comes from this machine (loopback) with no browser Origin header and
    no X-Forwarded-For (so a browser page, or a LAN client going through the
    Vite dev proxy, can't pose as the camera).
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from recallai.memory import users

router = APIRouter()

DEVICE_KEY_ENV = "RECALLAI_DEVICE_KEY"
DEVICE_KEY_HEADER = "X-RecallAI-Device-Key"
LOGIN_FAILED = "Incorrect username or password."


# ---------------------------------------------------------------------------
# Failed sign-in throttle (in memory, per username)
# ---------------------------------------------------------------------------
class LoginThrottle:
    """After `limit` failures for a username within `window` seconds, refuse
    further attempts for `lockout` seconds. Keyed by the submitted name, so
    it behaves the same whether or not the account exists."""

    def __init__(self, limit=5, window=300.0, lockout=60.0, clock=time.monotonic):
        self.limit, self.window, self.lockout = limit, window, lockout
        self.clock = clock
        self._lock = threading.Lock()
        self._failures = {}      # name -> [monotonic times]
        self._locked_until = {}  # name -> monotonic time

    def retry_after(self, name):
        """Seconds until `name` may try again (0 = allowed now)."""
        with self._lock:
            until = self._locked_until.get(name, 0.0)
            left = until - self.clock()
            if left <= 0:
                self._locked_until.pop(name, None)
                return 0
            return int(left) + 1

    def failed(self, name):
        now = self.clock()
        with self._lock:
            recent = [t for t in self._failures.get(name, []) if now - t < self.window]
            recent.append(now)
            if len(recent) >= self.limit:
                self._locked_until[name] = now + self.lockout
                recent = []
            self._failures[name] = recent
            if len(self._failures) > 10_000:   # don't grow without bound
                self._failures.clear()

    def succeeded(self, name):
        with self._lock:
            self._failures.pop(name, None)
            self._locked_until.pop(name, None)

    def reset(self):
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()


throttle = LoginThrottle()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
def _unauthorized(detail="Sign in required."):
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


def bearer_token(request: Request):
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


def _user_from(token):
    user = users.user_for_token(token) if token else None
    if user is None:
        raise _unauthorized("Sign in required." if not token
                            else "Your session has expired. Sign in again.")
    return user


def current_user(request: Request):
    """The signed-in user (Bearer token), else 401."""
    user = _user_from(bearer_token(request))
    request.state.user = user
    return user


def media_user(request: Request):
    """Like current_user, but also accepts ?token= for <img>/MJPEG URLs."""
    token = bearer_token(request) or request.query_params.get("token")
    user = _user_from(token)
    request.state.user = user
    return user


def _is_loopback(host):
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


def device_allowed(request: Request) -> bool:
    key = os.environ.get(DEVICE_KEY_ENV, "").strip()
    sent = request.headers.get(DEVICE_KEY_HEADER, "")
    if key and sent and hmac.compare_digest(sent.encode(), key.encode()):
        return True
    host = request.client.host if request.client else None
    return (_is_loopback(host)
            and "origin" not in request.headers
            and "x-forwarded-for" not in request.headers)


def require_device(request: Request):
    """Camera/device-only routes (POST /api/live, GET /api/device/gps)."""
    if not device_allowed(request):
        raise HTTPException(
            403, "Device endpoint: only the local camera process (or a client "
                 f"sending {DEVICE_KEY_HEADER}) may call this.")
    return True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
class RegisterBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=users.MAX_PASSWORD)
    display_name: str = Field(default="", max_length=200)


class LoginBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024)


def _session_payload(user, token, expires):
    return {"token": token, "token_type": "bearer", "expires_at": expires, "user": user}


def _belongings_dir():
    # Read at call time so tests (and RECALLAI_BELONGINGS_DIR) are honoured.
    from recallai.api import search
    return search.BELONGINGS_DIR


@router.post("/api/auth/register", status_code=201)
async def register(body: RegisterBody):
    """Create an account and sign it in. The first account ever created
    claims all data recorded before accounts existed."""
    try:
        user, claimed = await run_in_threadpool(
            users.create_user, body.username, body.password, body.display_name,
            None, _belongings_dir())
    except users.AccountError as e:
        status = 409 if "taken" in str(e) else 422
        raise HTTPException(status, {"message": str(e), "field": e.field})
    token, expires = users.create_session(user["id"])
    out = _session_payload(user, token, expires)
    out["claimed"] = claimed
    return out


@router.post("/api/auth/login")
async def login(body: LoginBody):
    name = users.normalize_username(body.username)
    wait = throttle.retry_after(name)
    if wait:
        raise HTTPException(429, f"Too many failed sign-ins. Try again in {wait} s.",
                            headers={"Retry-After": str(wait)})
    user = await run_in_threadpool(users.authenticate, name, body.password)
    if user is None:
        throttle.failed(name)
        raise _unauthorized(LOGIN_FAILED)
    throttle.succeeded(name)
    token, expires = users.create_session(user["id"])
    return _session_payload(user, token, expires)


@router.post("/api/auth/logout")
def logout(request: Request, user=Depends(current_user)):
    users.revoke_session(bearer_token(request))
    return {"status": "signed out"}


@router.get("/api/auth/me")
def me(user=Depends(current_user)):
    return user
