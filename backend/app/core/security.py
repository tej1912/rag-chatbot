"""
Security helpers.

- Admin auth: `X-Admin-Key` header, constant-time compare. Fails CLOSED: if
  ADMIN_API_KEY isn't configured, admin endpoints refuse everything.
- Server-issued sessions: the client can no longer invent a session_id (which
  let attackers dodge per-session rate limits by rotating ids). POST
  /v1/session issues a signed token; /v1/chat only accepts valid tokens.
- Cloudflare Turnstile (free captcha): checked once when a session is issued.
  If TURNSTILE_SECRET is empty the check is skipped (dev mode).
"""
import hashlib
import hmac
import logging
import secrets
import time

import httpx
from fastapi import Header, HTTPException, Request

from app.config import settings

logger = logging.getLogger("security")

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TOKEN_VERSION = "v1"


# ---- admin auth ----

def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency guarding ingest/delete/list endpoints."""
    if not settings.admin_api_key:
        raise HTTPException(status_code=503, detail="Admin API key not configured on server")
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Key")


# ---- client ip ----

def client_ip(request: Request) -> str:
    """
    Real client IP. Behind a reverse proxy (Railway/Render/Cloudflare) uvicorn
    started with --proxy-headers rewrites request.client from X-Forwarded-For,
    so this stays correct without trusting raw headers in app code.
    """
    return request.client.host if request.client else "unknown"


# ---- signed session tokens ----
# token = "v1.<session_id>.<expiry_epoch>.<hmac_sha256_hex>"

def _sign(session_id: str, expiry: int) -> str:
    msg = f"{TOKEN_VERSION}.{session_id}.{expiry}".encode()
    return hmac.new(settings.effective_session_secret.encode(), msg, hashlib.sha256).hexdigest()


def issue_session() -> tuple[str, str, int]:
    """Returns (session_id, token, expires_in_seconds)."""
    session_id = secrets.token_urlsafe(18)
    expiry = int(time.time()) + settings.session_token_ttl_seconds
    token = f"{TOKEN_VERSION}.{session_id}.{expiry}.{_sign(session_id, expiry)}"
    return session_id, token, settings.session_token_ttl_seconds


def verify_session_token(token: str | None) -> str | None:
    """Returns the session_id if the token is valid and unexpired, else None."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != TOKEN_VERSION:
        return None
    _, session_id, expiry_str, signature = parts
    try:
        expiry = int(expiry_str)
    except ValueError:
        return None
    if expiry < time.time():
        return None
    if not hmac.compare_digest(signature, _sign(session_id, expiry)):
        return None
    return session_id


# ---- turnstile ----

def verify_turnstile(token: str | None, ip: str) -> bool:
    if not settings.turnstile_secret:
        return True  # captcha not configured (dev)
    if not token:
        return False
    try:
        resp = httpx.post(
            TURNSTILE_VERIFY_URL,
            data={"secret": settings.turnstile_secret, "response": token, "remoteip": ip},
            timeout=5,
        )
        resp.raise_for_status()
        return bool(resp.json().get("success"))
    except Exception:
        logger.exception("Turnstile verification call failed")
        return False  # fail closed: no captcha proof, no session
