"""
Simple local authentication + admin/user separation.

CLAUDE.md security requirements:
  - "admin routes require at least simple local authentication"
  - "separate administrator actions from normal chat actions"

Two bearer tokens (admin, user) provide a lightweight role split:
  - require_user  : accepts the user OR admin token  -> /chat, /classify, /events, /policy (read)
  - require_admin : accepts the admin token only      -> /approvals/*, /export/jsonl (privileged)

Tokens are read from a request's `Authorization: Bearer <token>` header (or `X-API-Key`).
Auth can be disabled with GATEWAY_AUTH_ENABLED=false (useful for some tests). Open routes:
`/health` and `/` (the UI shell) — the UI fetches authenticated endpoints with the token.
"""
from fastapi import Request, HTTPException

from gateway.settings import settings


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


def _role_for(token: str | None) -> str | None:
    if token is None:
        return None
    if token == settings.gateway_admin_token:
        return "admin"
    if token == settings.gateway_user_token:
        return "user"
    return None


def require_user(request: Request) -> str:
    """Allow user OR admin. Returns the resolved role."""
    if not settings.gateway_auth_enabled:
        return "admin"
    role = _role_for(_extract_token(request))
    if role is None:
        raise HTTPException(status_code=401, detail="missing or invalid token",
                            headers={"WWW-Authenticate": "Bearer"})
    return role


def require_admin(request: Request) -> str:
    """Allow admin only. 401 when no/invalid token, 403 when a valid non-admin token is used."""
    if not settings.gateway_auth_enabled:
        return "admin"
    token = _extract_token(request)
    role = _role_for(token)
    if role is None:
        raise HTTPException(status_code=401, detail="missing or invalid token",
                            headers={"WWW-Authenticate": "Bearer"})
    if role != "admin":
        raise HTTPException(status_code=403, detail="administrator role required")
    return role
