"""Authentication for the dashboard.

Accepted credentials (any one):
  1. Tailscale identity: `Tailscale-User-Login` header injected by `tailscale serve`. Only trusted when the TCP peer is
     loopback (i.e. it really came through the local tailscaled proxy) AND the login is in JARVIS_TAILSCALE_USERS.
  2. Signed session cookie obtained from POST /api/login with the shared JARVIS_AUTH_TOKEN.
  3. `Authorization: Bearer <token>` (scripts / curl).

Auth is disabled only for local development: no token, no trusted users, loopback bind.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, WebSocket

from . import config

COOKIE = "jarvis_session"
_ephemeral_key = secrets.token_bytes(32)  # used only if no secret configured


def enabled() -> bool:
    return bool(config.AUTH_TOKEN or config.TRUSTED_TAILSCALE_USERS)


def validate_startup() -> None:
    if not config.is_loopback(config.HOST) and not enabled():
        raise RuntimeError("Refusing to listen on a non-loopback address without JARVIS_AUTH_TOKEN "
                           "or JARVIS_TAILSCALE_USERS set.")
    if config.AUTH_TOKEN and len(config.AUTH_TOKEN) < 16:
        raise RuntimeError("JARVIS_AUTH_TOKEN must be at least 16 characters.")


def _key() -> bytes:
    if config.SESSION_SECRET:
        return config.SESSION_SECRET.encode()
    if config.AUTH_TOKEN:
        return hashlib.sha256(b"jarvis-session|" + config.AUTH_TOKEN.encode()).digest()
    return _ephemeral_key


def _sign(payload: str) -> str:
    return hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()


def make_cookie(user: str) -> str:
    exp = int(time.time()) + config.SESSION_DAYS * 86400
    payload = base64.urlsafe_b64encode(f"{user}|{exp}".encode()).decode()
    return f"{payload}.{_sign(payload)}"


def read_cookie(value: str | None) -> str | None:
    if not value or "." not in value:
        return None
    payload, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        user, exp = base64.urlsafe_b64decode(payload.encode()).decode().rsplit("|", 1)
        return user if int(exp) > time.time() else None
    except Exception:
        return None


def check_token(candidate: str | None) -> bool:
    return bool(config.AUTH_TOKEN and candidate and hmac.compare_digest(candidate.encode(), config.AUTH_TOKEN.encode()))


def _peer_is_loopback(conn) -> bool:
    return bool(conn.client and conn.client.host in ("127.0.0.1", "::1", "localhost"))


def identify(conn: Request | WebSocket) -> str | None:
    """Returns the authenticated actor name, or None."""
    if not enabled():
        return "local"
    login = conn.headers.get("tailscale-user-login", "").lower()
    if login and _peer_is_loopback(conn) and login in config.TRUSTED_TAILSCALE_USERS:
        return login
    user = read_cookie(conn.cookies.get(COOKIE))
    if user:
        return user
    auth = conn.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and check_token(auth[7:].strip()):
        return "token"
    return None


async def require_user(request: Request) -> str:
    user = identify(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    request.state.user = user
    return user


def origin_ok(ws: WebSocket) -> bool:
    """Block cross-site WebSocket hijacking: a browser Origin must match the Host it connected to."""
    origin = ws.headers.get("origin")
    if not origin:
        return True  # non-browser client (still needs credentials)
    host = origin.split("://", 1)[-1].split("/", 1)[0].lower()
    if host == ws.headers.get("host", "").lower():
        return True
    return any(origin.rstrip("/").lower() == o.rstrip("/").lower() for o in config.ALLOWED_ORIGINS)


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


chat_limiter = RateLimiter(config.RATE_LIMIT_PER_MIN)
login_limiter = RateLimiter(8)  # brute-force guard
